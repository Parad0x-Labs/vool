"""BLOCK-C seam 9: final-byte contract dominance (signed block 60da1f5f, Terra amendment 1).

t6: "Return only positions two, four, and five" shipped `... [receipt:listop]`.
t18: "Give me only the serial field" shipped `QP-771-AX [receipt:chip-recall]`.
The strict signal must derive from the QUESTION itself — the 8B extractor left
`format` empty on both turns, so format_orders alone can never carry the contract.
Under a strict contract the final bytes are the bare payload; receipts live in
the transcript record. Non-strict asks keep their decoration (negative control).
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


def test_t18_replay_only_field_ask_ships_bare_value(monkeypatch):
    """t18: chip recall under 'Give me only the serial field' — no [receipt:] in
    the answer bytes even though extraction left format empty."""
    facts = {"_chips": json.dumps([{"id": "cc", "kind": "field", "label": "serial field",
                                    "attr": None, "value": "QP-771-AX", "status": "live",
                                    "revision": 1}])}
    judge = _judge([_row("return the serial field", "recall")],
                   [{"obligation_id": "ob1", "text": "let me check", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Give me only the serial field.", EffectRunner(mode="record"),
        None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "QP-771-AX" in ans
    assert "[receipt:" not in ans, f"strict ask must ship bare bytes: {ans!r}"
    assert "[" not in ans.replace("QP-771-AX", ""), f"no decoration at all: {ans!r}"


def test_t6_replay_only_positions_ask_ships_undecorated_list(monkeypatch):
    """t6: list positions under 'Return only positions two, four, and five' —
    the listop receipt is forbidden extra text."""
    fruit = [{"id": f"f{i}", "kind": "list", "label": "fruits", "attr": i,
              "value": v, "status": "live", "revision": 1}
             for i, v in enumerate(["Apple", "Banana", "Cherry", "Date", "Elderberry"], 1)]
    facts = {"_chips": json.dumps(fruit)}
    judge = _judge([_row("positions two four five of the fruit list", "recall")],
                   [{"obligation_id": "ob1", "text": "checking", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "From my fruit list, return only positions two, four, and five.",
        EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "Banana" in ans and "Date" in ans and "Elderberry" in ans
    assert "[receipt:" not in ans, f"strict ask must ship bare bytes: {ans!r}"


def test_nonstrict_ask_keeps_receipt_decoration(monkeypatch):
    """Negative control: an ordinary ask (no only/exactly/nothing-else) keeps its
    provenance decoration — seam 9 must not strip the world."""
    facts = {"_chips": json.dumps([{"id": "dd", "kind": "field", "label": "build number",
                                    "attr": None, "value": "4471", "status": "live",
                                    "revision": 1}])}
    judge = _judge([_row("recall the build number", "recall")],
                   [{"obligation_id": "ob1", "text": "let me check", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What build number did I mention earlier?", EffectRunner(mode="record"),
        None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "4471" in ans
    assert "[receipt:" in ans or "receipt:" in transcript, \
        f"non-strict answers keep provenance: {ans!r}"


def test_strict_signal_ignores_incidental_only(monkeypatch):
    """'the only city' mid-sentence is not an output contract — decoration stays."""
    judge = _judge([_row("compute 12 * 12", "arithmetic", "12 * 12")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "If the only city on the island has 12 districts of 12 blocks each, "
        "how many blocks are there?", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "144" in ans
    assert "receipt:" in transcript      # provenance survives somewhere in the record
