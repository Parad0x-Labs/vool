"""ROUND-010 pins — Option A: candidate-preserving +1 repair for exact_words.

Frozen repair boundary (council/round-010/FIX_PLAN.md, ROOT FROZEN 2026-08-24):
  - exact_words deficit == +1  -> retry presents the count as an operable transform
    (candidate verbatim + N + M + "keep all your own words and their order, add exactly
    1 more word of your choosing"). Content-neutral: the kernel authors no semantic word.
  - deficit >= +2              -> unchanged generic rejection, then FORMAT_UNSATISFIED.
  - a +1 whose repair still misses -> unchanged exhaustion -> FORMAT_UNSATISFIED.

Everything else is untouched: canonical word gate is the sole validator, commit path,
3-round deterministic retry bound, overrun truncation, mint/ack, TTL, char gate,
non-word lanes.

These pins drive run_turn with fake judges, exactly like the round-004/008 families.
The judge models the measured qwen2.5:7b behaviour: it emits an (N-1)-word candidate
until it sees the delta directive, then emits an N-word candidate that KEEPS the
original words in order plus one added word — the deterministic behaviour R3/R5
established for +1 deficits.
"""
from __future__ import annotations

import json
import re

import pytest

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner

DELTA_MARK = "add exactly 1 more word"


def _judge_plus_one(n_words_short: int, repaired: str):
    """Emit an (N-1)-word candidate; once the delta directive appears in the prompt,
    emit the repaired N-word candidate."""
    state = {"repaired": False}
    prompts: list[str] = []

    def fake(runner, effect_id, system, user):
        if effect_id == "model.synthesize":
            prompts.append(user)
        if effect_id == "model.extract":
            return {"obligations": [{"description": "explain it", "lane": "knowledge",
                                     "query": "", "format": "", "source_offset": 0,
                                     "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            if DELTA_MARK in user and not state["repaired"]:
                state["repaired"] = True
                return {"claims": [{"obligation_id": "ob1", "text": repaired,
                                    "type": "unverified"}]}
            return {"claims": [{"obligation_id": "ob1",
                                "text": " ".join(repaired.split()[:n_words_short]),
                                "type": "unverified"}]}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    return fake


def _judge_fixed(text: str):
    """Always emit the same candidate, regardless of feedback (measured temp-0 attractor)."""
    prompts: list[str] = []

    def fake(runner, effect_id, system, user):
        if effect_id == "model.synthesize":
            prompts.append(user)
        if effect_id == "model.extract":
            return {"obligations": [{"description": "explain it", "lane": "knowledge",
                                     "query": "", "format": "", "source_offset": 0,
                                     "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": [{"obligation_id": "ob1", "text": text,
                                "type": "unverified"}]}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    fake.prompts = prompts
    return fake


def _turn(monkeypatch, judge, contract, question="Why does it happen?", facts=None,
           capture=None):
    monkeypatch.setattr(repl, "_model_json", judge)
    f = {repl._LEDGER_KEY: json.dumps(contract)} if contract else (facts or {})
    transcript, journal, _l, facts_out = repl.run_turn(
        question, EffectRunner(mode="record"), None, None, session_facts=f or None)
    if capture is not None:
        capture.extend(str(e.get("result", "")) for e in journal.entries()
                       if e.get("effect_id") == "model.synthesize")
    return transcript, repl._extract_answer(transcript) or "", facts_out


def _synth_prompts(judge):
    """Re-run helper access to the prompts the judge actually received."""
    return getattr(judge, "prompts", []) if hasattr(judge, "prompts") else []


def _ledger(n, ttl=2):
    return [{"kind": "exact_words", "arg": str(n), "ttl": ttl}]


def _bare(ans: str) -> str:
    return re.sub(r" \[(?:unverified - model memory|stipulated|receipt:[^\]]+)\]\s*$", "", ans).strip()


# ===================================================== A. known positive +1 repro
def test_A_forest_class_plus_one_converges(monkeypatch):
    """The tape's turn-012 shape: a 5-word candidate under exactly-6 converges when the
    retry presents the operable transform, and commits the model's own words + one."""
    repaired = "a large area where trees grow"           # 6 words: original 5 in order + 'where'
    judge = _judge_plus_one(5, repaired)
    tr, ans, facts = _turn(monkeypatch, judge, _ledger(6),
                           question="define forest")
    assert _bare(ans) == "a large area where trees grow", (
        f"the +1 repair must converge and commit: {ans!r}")
    assert "COMMIT: committed" in tr
    # provenance: every original word kept, in order
    assert "a large area trees grow"[:11] in ans or all(
        w in ans.split() for w in ["a", "large", "area", "trees", "grow"])


# ===================================================== B. fresh unseen +1 variants
@pytest.mark.parametrize("short,repaired,n", [
    ("water flows in a river channel", "water flows in a wide river channel", 7),   # define river n=7
    ("big body of salty water", "a big body of salty water", 6),                   # define ocean n=6
    ("opposite poles pull together", "opposite poles pull each together", 5),      # magnets n=5
    ("mass pulls objects down", "mass pulls small objects down", 5),                # gravity n=5
    ("iron reacts with oxygen water", "iron reacts with oxygen and water", 6),      # rust n=6
])
def test_B_fresh_plus_one_variants_converge(monkeypatch, short, repaired, n):
    judge = _judge_plus_one(len(short.split()), repaired)
    tr, ans, _ = _turn(monkeypatch, judge, _ledger(n))
    assert _bare(ans) == repaired, f"+1 variant failed to converge: {ans!r}"
    assert "COMMIT: committed" in tr
    assert "Cannot answer this turn" not in ans


# ===================================================== C. deficit >= 2 stays fail-closed
@pytest.mark.parametrize("cand,n", [
    ("joyful state of contentment", 6),      # deficit 2 (the happy repro)
    ("ice floats because less dense than water", 9),  # deficit 2
    ("bread turns moldy due to fungi growth", 11),    # deficit 4
])
def test_C_deficit_two_plus_fails_closed(monkeypatch, cand, n):
    judge = _judge_fixed(cand)
    tr, ans, facts = _turn(monkeypatch, judge, _ledger(n))
    assert cand not in ans.split("\n")[-1] or "COMMIT: committed" not in tr, (
        "a deficit >=2 candidate must not commit")
    assert "couldn’t meet the exact " in ans and "-word requirement" in ans
    assert "FORMAT_UNSATISFIED" in tr
    # S2-grade boundary pin: the +1 directive must never ENTER A MODEL PROMPT for
    # deficit >= 2 (the directive lives in the prompt, not the transcript).
    leaked = [p for p in judge.prompts if DELTA_MARK in p]
    assert not leaked, (
        f"the +1 directive fired for a deficit >= 2 case: directive leaked into "
        f"{len(leaked)} synth prompt(s)")


# ===================================================== C2. a +1 repair that still misses
def test_C2_failed_plus_one_repair_fails_closed(monkeypatch):
    """The repair is an offer, not a guarantee: if the model still emits (N-1) words
    on every round, exhaustion fail-closes exactly as before."""
    judge = _judge_fixed("voltage drops quite quickly")   # 4 words vs 5
    tr, ans, facts = _turn(monkeypatch, judge, _ledger(5))
    assert "couldn’t meet the exact 5-word requirement" in ans
    assert "FORMAT_UNSATISFIED" in tr and "best model effort 4 of 5 words" in tr


# ===================================================== D. already-exact commits untouched
def test_D_exact_candidate_commits_with_no_repair_activity(monkeypatch):
    judge = _judge_fixed("voltage simply drops quite quickly")   # exactly 5
    tr, ans, _ = _turn(monkeypatch, judge, _ledger(5))
    assert _bare(ans) == "voltage simply drops quite quickly"
    assert "COMMIT: committed" in tr
    assert DELTA_MARK not in tr and "claim rejected" not in tr


# ===================================================== E. over-count truncates (unchanged)
def test_E_overrun_truncates_and_commits(monkeypatch):
    judge = _judge_fixed("voltage drops quite quickly under heavy load")  # 7 vs 5
    tr, ans, _ = _turn(monkeypatch, judge, _ledger(5))
    assert len(ans.split()) == 5, f"overrun must truncate: {ans!r}"
    assert "COMMIT: committed" in tr
    assert "Cannot answer this turn" not in ans


# ===================================================== F. non-exact_words lanes untouched
def test_F1_no_contract(monkeypatch):
    judge = _judge_fixed("voltage drops quickly under load")
    tr, ans, _ = _turn(monkeypatch, judge, None)
    assert _bare(ans) == "voltage drops quickly under load"
    assert DELTA_MARK not in tr and "Cannot answer this turn" not in ans


def test_F2_lowercase_only_contract(monkeypatch):
    judge = _judge_fixed("Warm Sun Drives The Current")
    tr, ans, _ = _turn(monkeypatch, judge,
                       [{"kind": "lowercase", "arg": "", "ttl": 2}])
    assert _bare(ans) == "warm sun drives the current"
    assert DELTA_MARK not in tr


def test_F3_end_word_contract(monkeypatch):
    judge = _judge_fixed("warm sun drives it")
    tr, ans, _ = _turn(monkeypatch, judge,
                       [{"kind": "end_word", "arg": "done", "ttl": 2}])
    assert ans.rstrip().endswith("done.")
    assert DELTA_MARK not in tr


def test_F4_exact_chars_contract_unchanged(monkeypatch):
    judge = _judge_fixed("short text here")
    tr, ans, _ = _turn(monkeypatch, judge, None, facts={})
    # char contracts flow through format_orders, not the ledger; a full char-contract
    # pin already exists in round-008 tests — here only assert the delta directive
    # never leaks into non-word lanes.
    assert DELTA_MARK not in tr


# ===================================================== H. byte/provenance/invariant checks
def test_H1_repaired_answer_preserves_model_words_in_order(monkeypatch):
    repaired = "a large area where trees grow"
    judge = _judge_plus_one(5, repaired)
    tr, ans, _ = _turn(monkeypatch, judge, _ledger(6), question="define forest")
    words = _bare(ans).split()
    original = ["a", "large", "area", "trees", "grow"]
    # original words appear in their original relative order
    idx = [words.index(w) for w in original if w in words]
    assert idx == sorted(idx) and len(idx) == 5, (
        f"model-owned words must be preserved in order: {words!r}")


def test_H2_canonical_validator_is_sole_acceptor(monkeypatch):
    """The repaired candidate is accepted ONLY through the same gate: if the 'repaired'
    candidate is still (N-1) words, it is rejected again even though it followed the
    directive (covered functionally by C2; here assert the gate count in transcript)."""
    judge = _judge_fixed("a large area trees grow")
    tr, ans, _ = _turn(monkeypatch, judge, _ledger(6), question="define forest")
    assert "5 words against an exactly-6-words order" in tr
    assert "FORMAT_UNSATISFIED" in tr and "best model effort 5 of 6 words" in tr
    assert "couldn’t meet the exact 6-word requirement" in ans


def test_H3_ttl_decrements_once_regardless_of_repair_outcome(monkeypatch):
    repaired = "a large area where trees grow"
    judge = _judge_plus_one(5, repaired)
    tr, ans, facts = _turn(monkeypatch, judge, _ledger(6, ttl=3), question="define forest")
    assert json.loads(facts[repl._LEDGER_KEY]) == [
        {"kind": "exact_words", "arg": "6", "ttl": 2}], (
        f"TTL must decrement exactly once on a repaired commit: {facts.get(repl._LEDGER_KEY)!r}")


def test_H4_retry_bound_is_unchanged(monkeypatch):
    """Repair happens within the SAME 3-round budget: a judge that repairs on round 2
    shows exactly one delta-directive rejection then convergence — never extra calls."""
    repaired = "a large area where trees grow"
    judge = _judge_plus_one(5, repaired)
    tr, ans, _ = _turn(monkeypatch, judge, _ledger(6), question="define forest")
    assert tr.count("claim rejected: 5 words against") == 1, (
        "the +1 repair must converge within the existing retry budget")

