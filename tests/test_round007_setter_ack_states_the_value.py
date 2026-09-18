"""ROUND-007 Root B — the setter ack must state each constraint's VALUE, not only its kind.

Frozen root (council/round-007/MISALIGNMENT_MATRIX.md §3, the operator's literal complaint): the ack
rendered `e["kind"]` and dropped `e["arg"]`, so `exact_words(4)` acknowledged as "exact words" — the
user was told a constraint KIND was accepted but never the N. "exactly four words" and "exactly nine
words" produced identical acks. The operator: "the acknowledgement often says merely 'exact words' …
without confirming N … not trustworthy enough for me to know which parts were accepted."

`_describe_mint` now renders the value; the ack reads "exactly 4 words". The pins drive run_turn end
to end and assert the N reaches the visible ack.

SABOTAGE: revert `_describe_mint` to `e["kind"].replace("_"," ")` -> the ack drops the N -> RED.
"""
from __future__ import annotations

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _ack_judge():
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


def _ack_for(monkeypatch, question):
    monkeypatch.setattr(repl, "_model_json", _ack_judge())
    transcript, _j, _l, _f = repl.run_turn(question, EffectRunner(mode="record"), None, None)
    return repl._extract_answer(transcript) or ""


# ------------------------------------------------ unit: the describer carries the value
def test_describe_mint_renders_the_value():
    assert repl._describe_mint({"kind": "exact_words", "arg": "4"}) == "exactly 4 words"
    assert repl._describe_mint({"kind": "exact_words", "arg": "9"}) == "exactly 9 words"
    assert repl._describe_mint({"kind": "lowercase", "arg": ""}) == "lowercase"


def test_two_different_counts_do_not_acknowledge_identically():
    """The defect in one line: without the value, N=4 and N=9 were indistinguishable in the ack."""
    assert repl._describe_mint({"kind": "exact_words", "arg": "4"}) \
        != repl._describe_mint({"kind": "exact_words", "arg": "9"})


# ------------------------------------------------ end-to-end: the N reaches the visible ack
def test_setter_ack_states_the_word_count(monkeypatch):
    ack = _ack_for(monkeypatch, "Starting with the next THREE answers, use exactly four words in each reply.")
    assert ack.startswith("noted —"), f"deterministic ack expected: {ack!r}"
    assert "exactly 4 words" in ack, f"the ack must confirm the value N=4, not just 'exact words': {ack!r}"


def test_compound_ack_names_every_armed_constraint_with_its_value(monkeypatch):
    ack = _ack_for(monkeypatch, "make my next two replies exactly six lowercase words each.")
    assert "exactly 6 words" in ack, f"the word count and its value must appear: {ack!r}"
    assert "lowercase" in ack, f"the lowercase constraint must also appear: {ack!r}"


def test_ack_scope_count_is_still_present(monkeypatch):
    """Root B must not drop the 'next N answers' scope the ack already carried."""
    ack = _ack_for(monkeypatch, "Starting with the next THREE answers, use exactly four words in each reply.")
    assert "next 3 answer" in ack, f"the scope (next 3 answers) must remain: {ack!r}"
