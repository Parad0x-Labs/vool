"""BLOCK-C seam 2: chip precedence + identity (signed block 60da1f5f).

t28: a stale generic-label chip (count=73) displaced a fresh correct computation
(714). t7-9: a lettered-colon list never minted, so robot ops mutated the fruit
list. Precedence: fresh results always beat stored state. Identity: ops bind only
to a noun-compatible live list; a mismatch refuses and changes NOTHING.
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


def test_t28_replay_fresh_computation_beats_stale_chip(monkeypatch):
    facts = {"_chips": json.dumps([{"id": "aa", "kind": "field", "label": "count",
                                    "attr": None, "value": "73", "status": "live",
                                    "revision": 1}])}
    judge = _judge([_row("final bolt count", "arithmetic", "860 - 275 + 148 - 19")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "A warehouse begins with 860 bolts... ships 275, receives 148, discards 19. "
        "How many bolts remain? Return only the final bolt count.",
        EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = repl._extract_answer(transcript)
    assert "714" in ans
    assert "73" not in ans.replace("714", "")     # the stale chip never ships


def test_generic_single_token_label_never_binds(monkeypatch):
    facts = {"_chips": json.dumps([{"id": "aa", "kind": "field", "label": "count",
                                    "attr": None, "value": "73", "status": "live",
                                    "revision": 1}])}
    judge = _judge([_row("how many words count", "chat")],
                   [{"obligation_id": "ob1", "text": "Which count do you mean?",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Tell me the count.", EffectRunner(mode="record"), None, None,
        session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "73" not in ans                        # generic label alone never recalls


def test_specific_multiword_label_still_recalls(monkeypatch):
    facts = {"_chips": json.dumps([{"id": "bb", "kind": "field", "label": "ticket id",
                                    "attr": None, "value": "VX-8042", "status": "live",
                                    "revision": 1}])}
    judge = _judge([_row("return the ticket id", "recall")],
                   [{"obligation_id": "ob1", "text": "inventory dump", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Return just the ticket ID and nothing else.", EffectRunner(mode="record"),
        None, None, session_facts=facts)
    assert "VX-8042" in (repl._extract_answer(transcript) or "")


def test_lettered_colon_list_mints(monkeypatch):
    judge = _judge([_row("create robot names", "compose")],
                   [{"obligation_id": "ob1",
                     "text": "A: Zylarion, B: Vexorion, C: Krytonix, D: Teralis, "
                             "E: Virex, F: Solvora, G: Netharion",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _, _, _, nf = repl.run_turn(
        "Create seven fictional robot names labeled A through G.",
        EffectRunner(mode="record"), None, None, session_facts={})
    chips = json.loads(nf["_chips"])
    vals = [c["value"] for c in chips if c["kind"] == "list"]
    assert len(vals) == 7 and vals[0] == "Zylarion"
    assert "robot names" in chips[0]["label"]     # noun label minted


def test_noun_mismatch_op_refuses_and_mutates_nothing(monkeypatch):
    """t8 replay: robot ops against a live FRUIT list refuse; fruits unchanged."""
    fruit = [{"id": f"f{i}", "kind": "list", "label": "fruits", "attr": i,
              "value": v, "status": "live", "revision": 1}
             for i, v in enumerate(["Apple", "Banana", "Cherry", "Date", "Elderberry"], 1)]
    facts = {"_chips": json.dumps(fruit)}
    judge = _judge([_row("delete robot entries", "compose")],
                   [{"obligation_id": "ob1", "text": "deleted!", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, nf = repl.run_turn(
        "Delete B and F from the robot list, then move G to the front.",
        EffectRunner(mode="record"), None, None, session_facts=facts)
    ans = repl._extract_answer(transcript) or ""
    assert "nothing was changed" in ans.lower() or "does not match" in ans.lower()
    after = [c["value"] for c in json.loads(nf["_chips"]) if c["kind"] == "list"]
    assert after == ["Apple", "Banana", "Cherry", "Date", "Elderberry"]   # untouched
