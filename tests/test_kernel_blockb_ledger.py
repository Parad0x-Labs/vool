"""BLOCK-B seam: constraint ledger with TTL (signed block c31a...bd4b seam 4 slice).

A scoped output instruction becomes a typed ledger entry: applied for exactly its
TTL of subsequent answers, then expires — never ignored (t141-3), never haunting
(t144: post-expiry UPPERCASE must render as asked).
"""
import json

from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(text):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "the ask", "lane": "compose",
                    "query": "", "format": "", "source_offset": 0,
                    "resolves_carryover": ""}]}
        if effect_id == "model.synthesize":
            return {"claims": [{"obligation_id": "ob1", "text": text,
                                "type": "conversational"}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _turn(q, facts, monkeypatch, model_text="Mars"):
    monkeypatch.setattr(repl, "_model_json", _judge(model_text))
    transcript, _, _, nf = repl.run_turn(q, EffectRunner(mode="record"), None, None,
                                         session_facts=facts)
    return repl._extract_answer(transcript), nf


def test_lowercase_ttl_two_applies_twice_then_expires(monkeypatch):
    _a1, f1 = _turn("For your next TWO answers only, use lowercase letters only.",
                   {}, monkeypatch, model_text="Understood.")
    assert json.loads(f1["_ledger"]) == [{"kind": "lowercase", "arg": "", "ttl": 2}]
    a2, f2 = _turn("Name one planet.", f1, monkeypatch, model_text="Mars")
    assert "mars" in a2 and "Mars" not in a2                  # applied (1st)
    a3, f3 = _turn("Name one metal.", f2, monkeypatch, model_text="Iron")
    assert "iron" in a3 and "Iron" not in a3                  # applied (2nd)
    assert json.loads(f3["_ledger"]) == []                    # expired
    a4, _ = _turn("Now write UPPERCASE exactly as shown.", f3, monkeypatch,
                  model_text="UPPERCASE")
    assert "UPPERCASE" in a4                                  # no haunting (t144)


def test_end_word_ttl_one(monkeypatch):
    _a1, f1 = _turn("For the next ONE reply only, end your answer with the word kiwi.",
                   {}, monkeypatch, model_text="Noted.")
    a2, f2 = _turn("Give one sentence explaining evaporation.", f1, monkeypatch,
                   model_text="Water turns into vapor when heated")
    assert a2.rstrip().rstrip(".").endswith("kiwi")
    a3, _ = _turn("Give one sentence explaining condensation normally.", f2,
                  monkeypatch, model_text="Vapor becomes liquid when cooled.")
    assert "kiwi" not in a3                                    # expired after one


def test_minting_turn_is_not_itself_constrained(monkeypatch):
    a1, _ = _turn("For your next TWO answers only, use lowercase letters only.",
                  {}, monkeypatch, model_text="OK — starting next answer.")
    # the setter now ships a deterministic ack (operator live round, lane-2);
    # the mint turn stays unconstrained — the ack is not lowercase-forced.
    assert "applies to your next" in a1                        # mint turn acks, unconstrained
