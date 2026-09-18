"""ROUND-007 Root A — a spelled count above five must arm exact_words, not silently vanish.

Frozen root (council/round-007/MISALIGNMENT_MATRIX.md §1): turn 002 asked "exactly SIX lowercase
words". The count parser knew only one..five (`(one|two|three|four|five|\\d+)`), so exact_words(6)
never minted — only lowercase armed, and the two in-scope answers ran with the word count
unenforced. A sibling frame regex already knew six..ten, so the file disagreed with itself. The
council falsified the competing "adjacency" thesis by probe: "exactly FOUR lowercase words" mints
(four + adjective), "exactly SIX words" does not (six, no adjective) — the number word, not the
adjective, was the gap.

The pin drives the real `_mint_ledger` for the vocabulary range and the real `run_turn` for the
end-to-end arming, and asserts the answer-noun is still required so widening the count cannot widen
false positives. Nothing reimplements the regex.

SABOTAGE: revert the alternation to `(one|two|three|four|five|\\d+)` -> the six.. cases go RED
(exact_words absent), while five and the negative controls stay green.
"""
from __future__ import annotations

import json

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _kinds(q):
    return {e["kind"]: e.get("arg", "") for e in repl._mint_ledger(q)}


# ----------------------------------------------------------------- the tape, and the whole gap
def test_six_lowercase_words_now_arms_both_constraints():
    """Turn 002 verbatim shape: exactly six lowercase words -> BOTH lowercase and exact_words(6)."""
    k = _kinds("After this acknowledgement, make my next two replies exactly six lowercase words each.")
    assert k.get("exact_words") == "6", f"exact_words(6) must arm from 'six': {k}"
    assert "lowercase" in k, f"lowercase must still arm alongside it: {k}"


def test_the_number_word_gap_six_through_twenty_is_closed():
    for word, n in (("six", "6"), ("seven", "7"), ("eight", "8"), ("nine", "9"),
                    ("ten", "10"), ("twelve", "12"), ("twenty", "20")):
        k = _kinds(f"for your next two replies use exactly {word} words each")
        assert k.get("exact_words") == n, f"'{word}' must arm exact_words({n}): {k}"


def test_five_and_digits_are_unregressed():
    assert _kinds("next two replies exactly five words each").get("exact_words") == "5"
    assert _kinds("next two replies exactly 6 words each").get("exact_words") == "6"
    assert _kinds("next two replies exactly 15 words each").get("exact_words") == "15"


# ----------------------------------------------------------------- widening count must not widen FPs
def test_answer_noun_is_still_required():
    """'exactly six reasons' is not a word-count contract — the noun 'words' is still mandatory,
    so extending the number vocabulary introduces no new false positive."""
    assert "exact_words" not in _kinds("please list exactly six reasons why steel rusts")
    assert "exact_words" not in _kinds("we met exactly six times last spring")


def test_a_bare_count_with_no_frame_mints_nothing():
    """No next-N frame and no 'exactly N words' clause -> nothing arms (the empty-return guard)."""
    assert _kinds("tell me six facts about copper") == {}


# ----------------------------------------------------------------- end-to-end through run_turn
def _judge():
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "acknowledge", "lane": "chat", "query": "",
                                     "format": "", "source_offset": 0, "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": [{"obligation_id": "ob1", "text": "ok", "type": "conversational"}]}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    return fake


def test_setter_turn_persists_exact_words_six_to_the_ledger(monkeypatch):
    """The armed constraint must reach the carryover ledger the next turn will read — not just the
    local mint. This is the seam the operator saw fail: the count was absent downstream."""
    monkeypatch.setattr(repl, "_model_json", _judge())
    _t, _j, _led, facts = repl.run_turn(
        "After this acknowledgement, make my next two replies exactly six lowercase words each.",
        EffectRunner(mode="record"), None, None)
    # The minted contract persists for the NEXT turn via session_facts["_ledger"], not the 3rd
    # return value (which is the ACTIVE-this-turn ledger; a setter turn is itself unconstrained).
    persisted = json.loads((facts or {}).get(repl._LEDGER_KEY, "[]"))
    kinds = {e["kind"]: e.get("arg", "") for e in persisted}
    assert kinds.get("exact_words") == "6", f"the 6-word count must persist to the ledger: {kinds}"
    assert "lowercase" in kinds, f"lowercase must persist too: {kinds}"
