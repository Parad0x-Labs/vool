"""ROUND-004 pins — REPINNED for round-008 (fail-closed exact_words).

History: round-004 froze accept-and-degrade — an unmeetable word-count contract shipped the
model's best-fitting underrun with a visible shortfall footer. Round-008 (council/round-008/
FIX_PLAN.md, ROOT FROZEN 2026-08-23) overturned that resolution: PROBE1 showed the adjacent
character-count gate has ALWAYS fail-closed under the identical conditions, so the word gate's
final-round keep was an architectural outlier, and committing bytes the kernel itself proved
violate a HARD contract was a false installment. The frozen invariant now:

    a HARD exact_words contract must never commit substantive bytes the kernel has
    already deterministically proven violate that contract;

exhaustion terminates in the seam-10 liveness banner, truthfully named
("format unsatisfied: best model effort was M of N words"), never truncated by the ledger
transforms (PROBE2), never silent, and the TTL still decrements exactly once (D1 ruling:
the reply position is consumed, succeeded or not).

What survives from round-004 unchanged: the overrun truncation, the conforming-answer path,
inertness without a contract, and the honest empty-claim decline. What is repinned: every
expectation that a non-conforming underrun SHIPS. These are not string edits until green —
each repinned test asserts the new contract boundary, and reverting the round-008 gate
repair (restoring the final-round keep) turns them red naming that cause.
"""
from __future__ import annotations

import json

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner

CONTRACT = [{"kind": "exact_words", "arg": "4", "ttl": 2}]


def _judge(answer_text: str):
    """Extract one knowledge obligation; synthesize the SAME claim every round.

    Returning an identical claim on all three rounds is the measured behaviour: the tape shows
    the model repeating a 3-word answer through three informed retries.
    """
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "explain it", "lane": "knowledge",
                                     "query": "", "format": "", "source_offset": 0,
                                     "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": [{"obligation_id": "ob1", "text": answer_text,
                                "type": "conversational"}]}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    return fake


def _drive(monkeypatch, answer_text: str, contract=CONTRACT):
    monkeypatch.setattr(repl, "_model_json", _judge(answer_text))
    facts = {repl._LEDGER_KEY: json.dumps(contract)} if contract else {}
    transcript, _j, _led, facts_out = repl.run_turn(
        "Why does it happen?", EffectRunner(mode="record"), None, None, session_facts=facts)
    return transcript, repl._extract_answer(transcript), facts_out


# ------------------------------------------------- repinned: the underrun no longer ships
def test_an_underrun_fails_closed_instead_of_shipping(monkeypatch):
    """ROUND-008 boundary: a proven-non-conforming underrun must NOT become the committed
    substantive answer — the seam-10 liveness banner ships instead, truthfully named."""
    transcript, answer, _ = _drive(monkeypatch, "voltage drops quickly")      # 3 words vs 4
    assert answer is not None and answer.strip(), "the turn must not end in silence"
    assert "COMMIT: committed" not in transcript, "a malformed claim must never commit"
    assert "couldn’t meet the exact 4-word requirement" in answer, (
        f"the conversational fallback must name the actual target N: {answer!r}")
    assert "voltage drops quickly" in answer, "closest attempt is displayed, model-authored"
    assert "format unsatisfied: best model effort was 3 of 4 words" in transcript, (
        "internal truth must remain in the transcript/settle record")
    assert "no claim produced" not in transcript.split("\x01")[-1], (
        "a claim WAS produced and format-rejected; the record must not claim otherwise")


def test_the_fail_closed_banner_is_not_truncated_by_the_ledger(monkeypatch):
    """PROBE2 pin (ROUND-011 UX repin): the failure text passes outside
    `_apply_ledger_text`, so an armed exact_words(N) contract may not truncate the
    user-facing failure message to N words."""
    transcript, answer, _ = _drive(monkeypatch, "voltage drops quickly")
    assert len(answer.split()) > 4, (
        f"the failure message was truncated to the contract count: {answer!r}")
    assert "couldn\u2019t meet the exact 4-word requirement" in answer


def test_the_ttl_still_decrements_exactly_once_on_the_failed_turn(monkeypatch):
    """D1 ruling pin: the FORMAT_UNSATISFIED turn consumes its chronological reply position —
    per-substantive-turn accounting is untouched by the repair."""
    transcript, answer, facts_out = _drive(monkeypatch, "voltage drops quickly")
    ledger = json.loads(facts_out.get(repl._LEDGER_KEY, "[]"))
    assert ledger == [{"kind": "exact_words", "arg": "4", "ttl": 1}], (
        f"ttl must decrement exactly once (2 -> 1): {ledger!r}")


def test_the_informed_retries_ran_before_failing_closed(monkeypatch):
    """The retry loop structure is unchanged: rounds 1-2 still reject with named feedback
    before the final round fails closed."""
    transcript, answer, _ = _drive(monkeypatch, "voltage drops quickly")
    assert transcript.count("claim rejected: 3 words against an exactly-4-words order") == 2, (
        "the two informed rejection rounds must still run before fail-closed")


def test_the_longest_underrun_tie_break_is_gone_because_nothing_ships(monkeypatch):
    """Round-004's longest-underrun tie-break existed only to choose WHICH malformed answer
    shipped. Under fail-closed no underrun ships, so neither candidate may appear."""
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "explain it", "lane": "knowledge", "query": "",
                                     "format": "", "source_offset": 0, "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": [{"obligation_id": "ob1", "text": "two words", "type": "conversational"},
                               {"obligation_id": "ob1", "text": "exactly three words here",
                                "type": "conversational"}]}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    monkeypatch.setattr(repl, "_model_json", fake)
    facts = {repl._LEDGER_KEY: json.dumps([{"kind": "exact_words", "arg": "5", "ttl": 2}])}
    transcript, _j, _l, _f = repl.run_turn(
        "Why does it happen?", EffectRunner(mode="record"), None, None, session_facts=facts)
    answer = repl._extract_answer(transcript) or ""
    assert "COMMIT: committed" not in transcript, "no underrun may commit under a hard contract"
    assert "couldn\u2019t meet the exact 5-word requirement" in answer, (
        f"the conversational fallback must ship: {answer!r}")


# ------------------------------------------------------------------ nothing else may change
def test_a_conforming_answer_is_untouched(monkeypatch):
    """Negative control: an exact-count answer ships clean."""
    transcript, answer, _ = _drive(monkeypatch, "voltage simply drops quickly")   # exactly 4
    assert answer.strip() == "voltage simply drops quickly"
    assert "Cannot answer this turn" not in answer


def test_an_overrun_is_still_truncated(monkeypatch):
    """The t41 repair must keep working: an overrun is a compliant transform and commits."""
    transcript, answer, _ = _drive(monkeypatch, "voltage drops quickly under sustained heavy load")
    assert len((answer or "").split()) == 4, f"overrun was not truncated to the count: {answer!r}"
    assert "Cannot answer this turn" not in answer


def test_a_turn_with_no_contract_is_completely_untouched(monkeypatch):
    """The whole mechanism must be inert when no scoped contract is active."""
    transcript, answer, _ = _drive(monkeypatch, "voltage drops quickly", contract=None)
    assert "voltage drops quickly" in (answer or "")
    assert "exact-words render" not in transcript
    assert "Cannot answer this turn" not in (answer or "")


def test_an_empty_claim_still_fails_honestly(monkeypatch):
    """The fail-closed path applies to a PRODUCED claim; a turn with nothing to say still
    declines through the ordinary liveness terminal without a format-unsatisfied note."""
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "explain it", "lane": "knowledge", "query": "",
                                     "format": "", "source_offset": 0, "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": []}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    monkeypatch.setattr(repl, "_model_json", fake)
    facts = {repl._LEDGER_KEY: json.dumps(CONTRACT)}
    transcript, _j, _l, _f = repl.run_turn(
        "Why does it happen?", EffectRunner(mode="record"), None, None, session_facts=facts)
    assert "format unsatisfied" not in transcript, (
        "nothing was produced; the banner must not claim a format-rejected claim existed")
