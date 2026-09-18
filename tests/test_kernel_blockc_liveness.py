"""BLOCK-C seam 10: constrained-render liveness (signed block 60da1f5f, Terra amendment 2).

t41: "Describe fire" under an active exactly-three-words constraint shipped ZERO
bytes (answer_present=false, declared_unanswerable). A format constraint may
constrain a response; it may never convert model format failure into silence.
Every accepted answerable turn ships visible bytes — a conforming response or a
NAMED failure.
"""
import json

from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(extract_rows, synth_claims=None):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": extract_rows}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims or []}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _row(desc, lane, query=""):
    return {"description": desc, "lane": lane, "query": query, "format": "",
            "source_offset": 0, "resolves_carryover": ""}


_THREE_WORD_LEDGER = {"_ledger": json.dumps([{"kind": "exact_words", "arg": "3", "ttl": 2}])}


def test_t41_replay_constraint_failure_ships_named_visible_bytes(monkeypatch):
    """t41: synthesis produced nothing under the active three-word constraint —
    the turn must ship a NAMED failure, never answer_present=false."""
    judge = _judge([_row("describe fire", "chat")], [])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Describe fire.", EffectRunner(mode="record"), None, None,
        session_facts=dict(_THREE_WORD_LEDGER))
    ans = repl._extract_answer(transcript)
    assert ans is not None, "an answerable accepted turn never renders NO answer span"
    assert ans.strip(), f"visible bytes required, got empty: {ans!r}"
    # ROUND-011 UX repin: the user-facing span is the friendly fallback naming N; the
    # constraint identity (exact_words(3)) stays in the transcript/ledger record.
    assert "exact 3-word requirement" in ans, f"the failure names the active N: {ans!r}"
    assert "exact_words(3)" in transcript


def test_overrun_prose_is_mechanically_truncated_to_the_count(monkeypatch):
    """t40 adjacency: a 7-word single-line answer under exactly-three-words is
    truncated to the model's own first three words — transform, not invention."""
    judge = _judge([_row("describe snow", "chat")],
                   [{"obligation_id": "ob1",
                     "text": "White cold falls and drifts slowly down.",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Describe snow.", EffectRunner(mode="record"), None, None,
        session_facts=dict(_THREE_WORD_LEDGER))
    ans = (repl._extract_answer(transcript) or "").strip()
    assert len(ans.split()) == 3, f"exactly three words must ship: {ans!r}"
    assert ans.rstrip(".!?") == "White cold falls", f"the model's own words, in order: {ans!r}"


def test_underrun_is_never_padded_and_never_silent(monkeypatch):
    """The truncator never invents: a 2-word answer under exactly-three-words is repaired (model
    retried), and when repair never converges the turn FAILS CLOSED through the seam-10
    liveness banner — never padded with invented words, never silence.

    ROUND-008 SEMANTICS CHANGE (operator decision, council/round-008/FIX_PLAN.md, ROOT FROZEN
    2026-08-23). Round-004 had repinned this to accept-and-degrade: the model's own short text
    shipped with a shortfall declared. Round-008 overturned that resolution — PROBE1 showed the
    adjacent character-count gate has always fail-closed under identical conditions, so
    committing bytes the kernel itself proved violate a HARD contract was a false installment.

    The test's two load-bearing intents are UNCHANGED and still asserted below — never padded,
    never silent — as is the proof that repair was attempted. The resolution assertions now pin
    the fail-closed contract: the banner ships, truthfully names the format failure (a claim WAS
    produced and rejected — never "no claim produced"), and the model's short text does NOT
    ship. This is a deliberate behaviour change, not a loosened assertion: reverting the
    round-008 gate repair (restoring the final-round keep) turns the new assertions red, and
    tests/test_round004_underrun_fallback.py + tests/test_round008_exact_words_fail_closed.py
    pin the same behaviour independently.
    """
    judge = _judge([_row("describe ice", "chat")],
                   [{"obligation_id": "ob1", "text": "Frozen water.", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Describe ice.", EffectRunner(mode="record"), None, None,
        session_facts=dict(_THREE_WORD_LEDGER))
    ans = repl._extract_answer(transcript)
    assert ans is not None and ans.strip(), "underrun must not end in silence"
    assert "Frozen water and" not in ans, "no invented padding"
    assert "COMMIT: committed" not in transcript, "a proven non-conforming underrun must not commit"
    assert "couldn\u2019t meet the exact 3-word requirement" in ans, (
        f"the conversational fallback must ship with the actual N: {ans!r}")
    assert "Frozen water." in ans, "the closest model-authored attempt is displayed"
    assert "FORMAT_UNSATISFIED" in transcript and "best model effort 2 of 3 words" in transcript, (
        "internal FORMAT_UNSATISFIED truth must remain in the transcript/receipts")
    assert "no claim produced" not in ans, "a claim was produced and format-rejected"
    assert "2 words against an exactly-3-words order" in transcript  # repair was tried


def test_no_constraint_zero_claims_still_ships_visible_failure(monkeypatch):
    """Impossible-format control (Terra): even with NO active ledger, zero claims
    at the terminal render ships a visible named failure, never silence."""
    judge = _judge([_row("summarize the doc", "chat")], [])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Summarize the doc.", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript)
    assert ans is not None and ans.strip(), \
        f"zero-claim terminal must ship a named failure: {ans!r}"
    assert "cannot answer" in ans.lower() or "no claim" in ans.lower()
