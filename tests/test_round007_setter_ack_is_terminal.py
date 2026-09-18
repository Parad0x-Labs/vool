"""ROUND-007 Root C — a constraint-setter turn ships the deterministic ack and NOTHING else.

Frozen root (council/round-007/MISALIGNMENT_MATRIX.md §2): turn 006 ("use exactly four words … next
THREE answers") shipped:

    noted — exact words applies to your next 3 answer(s).
    Refused — cannot compute: operands ['3'] are not in your message or stored fields …

Extraction spawned a phantom arithmetic obligation from the frame count ("three replies total" ->
query "3 - 1"). It settled REFUSED, and the refusal-append loop — keyed on `_rendered_obs`, which on
a setter turn holds only the ack's owner — appended the refusal after the ack. Gemini and DeepSeek
located it precisely at the render boundary; the fix makes the ack terminal on a setter turn.

This pin REPLAYS turn 006's exact frozen model responses (extract + both derives + synthesize) through
the real run_turn, so the phantom refusal is produced authentically, not mocked. Nothing reimplements
the render.

SABOTAGE: remove the `_setter_ack` break in the refusal loop -> the "cannot compute" line reappears
in the answer -> RED.
"""
from __future__ import annotations

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner

# Verbatim from council/round-007/R0/turn_006.json model_calls.
_TURN006 = ("Starting with the next THREE substantive answers after this message, use exactly four "
            "words in each reply. Do not consume one of the three on this acknowledgement.")
_EXTRACT = {"obligations": [
    {"description": "acknowledge request", "lane": "compose", "query": "", "format": "",
     "source_offset": 0, "resolves_carryover": ""},
    {"description": "next answer starts now", "lane": "knowledge", "query": "", "format": "",
     "source_offset": 29, "resolves_carryover": ""},
    {"description": "three replies total", "lane": "arithmetic", "query": "3 - 1", "format": "",
     "source_offset": 58, "resolves_carryover": ""}]}
_DERIVE_1 = {"computations": [{"obligation_id": "ob2", "label": "next answer starts now", "expression": "{s7}"},
                              {"obligation_id": "ob3", "label": "three replies total", "expression": "3"}],
             "missing": []}
_DERIVE_2 = {"computations": [{"obligation_id": "ob2", "label": "next answer starts now", "expression": "true"},
                              {"obligation_id": "ob3", "label": "three replies total", "expression": "true"}],
             "missing": []}
_SYNTH = {"claims": [{"obligation_id": "ob1", "text": "Understood, proceeding.", "type": "conversational"}]}


def _replay():
    state = {"derive": 0}
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return _EXTRACT
        if effect_id == "model.derive":
            state["derive"] += 1
            return _DERIVE_1 if state["derive"] == 1 else _DERIVE_2
        if effect_id == "model.synthesize":
            return _SYNTH
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    return fake


def test_setter_ack_carries_no_phantom_refusal(monkeypatch):
    monkeypatch.setattr(repl, "_model_json", _replay())
    transcript, _j, _l, _f = repl.run_turn(_TURN006, EffectRunner(mode="record"), None, None)
    answer = repl._extract_answer(transcript) or ""
    assert answer.startswith("noted —"), f"the deterministic setter ack must ship: {answer!r}"
    assert "Refused" not in answer, f"a phantom refusal leaked into the setter ack: {answer!r}"
    assert "cannot compute" not in answer, (
        f"the frame-count compute refusal leaked into the setter ack: {answer!r}")
    assert "operands" not in answer, f"compute-lane noise leaked into the setter ack: {answer!r}"


def test_setter_ack_is_a_single_line(monkeypatch):
    """The whole point of a setter ack is a clean one-line confirmation. A second line means
    something collateral leaked in."""
    monkeypatch.setattr(repl, "_model_json", _replay())
    transcript, _j, _l, _f = repl.run_turn(_TURN006, EffectRunner(mode="record"), None, None)
    answer = (repl._extract_answer(transcript) or "").strip()
    assert answer.count("\n") == 0, f"the setter ack must be a single line, got:\n{answer!r}"
