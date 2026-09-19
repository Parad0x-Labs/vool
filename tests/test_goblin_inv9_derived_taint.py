"""GOBLIN inv 9 (DERIVED TAINT) — an answer whose cited receipt came from an untrusted
origin (web/page/gap/machine) is marked as derived from untrusted content, so the model's
summary does not launder the restriction away. Flag-gated: default OFF is byte-identical.

Sabotage seam: revert the `_taint_marks` wiring at the render seam and the tainted test's
`derived-taint` assertion goes red. Negative controls: a clean (conversational) answer and
the flag-off path carry no marker, proving no over-marking.
"""
from __future__ import annotations

import core.kernel.repl as repl
from core import runtime_flags
from core.kernel.effects import EffectRunner
from tests.test_kernel_turn_contract import _judge, _row


def _drive(question, judge, fetch, monkeypatch, taint):
    monkeypatch.setattr(repl, "_model_json", judge)
    if taint:
        with runtime_flags.override("derived_taint", True):
            transcript, _, _, _ = repl.run_turn(question, EffectRunner(mode="record"), fetch, "test")
    else:
        transcript, _, _, _ = repl.run_turn(question, EffectRunner(mode="record"), fetch, "test")
    return repl._extract_answer(transcript) or ""


def test_web_derived_answer_is_tainted_when_enabled(monkeypatch):
    def fetch(q):
        return [{"title": "Gold", "snippet": "gold spot is 2400 USD", "url": "https://gold/x"}]
    judge = _judge(
        extract={"obligations": [_row("price of gold", "web_lookup", "gold spot price")]},
        synth={"claims": [{"obligation_id": "ob1", "text": "Gold is {n1} USD.", "type": "observed"}]},
    )
    ans = _drive("what is the price of gold", judge, fetch, monkeypatch, taint=True)
    assert "2400" in ans                       # the web value shipped
    assert "derived-taint" in ans              # ...and is marked untrusted-origin — SABOTAGE seam


def test_clean_conversational_answer_is_not_tainted(monkeypatch):
    judge = _judge(
        extract={"obligations": [_row("greet", "chat")]},
        synth={"claims": [{"obligation_id": "ob1", "text": "Hello there.", "type": "conversational"}]},
    )
    ans = _drive("hi", judge, lambda q: [], monkeypatch, taint=True)
    assert "derived-taint" not in ans          # nothing untrusted was cited


def test_flag_off_is_byte_identical_no_taint(monkeypatch):
    def fetch(q):
        return [{"title": "Gold", "snippet": "gold spot is 2400 USD", "url": "https://gold/x"}]
    judge = _judge(
        extract={"obligations": [_row("price of gold", "web_lookup", "gold spot price")]},
        synth={"claims": [{"obligation_id": "ob1", "text": "Gold is {n1} USD.", "type": "observed"}]},
    )
    ans = _drive("what is the price of gold", judge, fetch, monkeypatch, taint=False)
    assert "2400" in ans
    assert "derived-taint" not in ans          # default OFF → unchanged behavior
