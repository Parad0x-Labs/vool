"""BLOCK-C acceptance: DeepSeek Flash's contrib_3 mutation battery — the
deterministic subset, run as pins per the signed block. The live-model
mutations (stall-repair, aborted-turn recall, UNKNOWN-slot realism, file
surfaces) ride the operator's smoke question list instead.
"""
import json

from core.kernel import repl
from core.kernel.effects import EffectRunner
from core.kernel.evidence_types import _number_tokens


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


def test_seam5_identifier_boundary_table():
    """Flash's lexer shake: identifiers never count; unit-glued and scientific
    forms keep their values."""
    assert _number_tokens("2FA") == set()
    assert _number_tokens("EC2 us-west-2") == set()
    assert _number_tokens("VX-8042") == set()
    assert _number_tokens("APIv2 uv2") == set()
    assert _number_tokens("0x1F") == set()          # hex artifact 0 never leaks
    assert _number_tokens("1.5e3") == {"1.5"}       # sci notation keeps its base (D8)
    assert _number_tokens("7.33e-05") == {"7.33"}
    assert _number_tokens("24GB at 5pm, 61x faster") == {"24", "5", "61"}
    assert _number_tokens("inventory 42 42") == {"42"}


def test_seam7_time_battery():
    """7-a/b/c/e: carry, two-component duration, unitless refusal, wrap."""
    assert repl._eval_time("00:15 + 45 minutes", "At 00:15, what is it in 45 minutes?") == "01:00"
    assert repl._eval_time("14:20 + 1:30", "It is 14:20; add 1:30.") == "15:50"
    assert repl._eval_time("23:50 + 65 minutes", "At 23:50, in 65 minutes?") == "00:55"
    # 7-c: a unitless "+ 3" must NOT assume minutes
    assert repl._eval_time("09:15 + 3", "At 09:15, add 3.") is None


def test_seam7_power_and_sqrt_fractions():
    """7-d: fractional powers and sqrt are numbers, not identifiers."""
    assert repl._eval_arith("sqrt(0.25)") == 0.5
    assert repl._eval_arith("4^0.5") == 2.0


def test_y2b_list_member_beats_chip_value_collision(monkeypatch):
    """A field chip (count=5) and a list containing '5' share the token — the
    list op binds 5 as a MEMBER position, never the chip value."""
    chips = [{"id": "c1", "kind": "field", "label": "count", "attr": None,
              "value": "5", "status": "live", "revision": 1}]
    chips += [{"id": f"l{i}", "kind": "list", "label": "numbers", "attr": i,
               "value": v, "status": "live", "revision": 1}
              for i, v in enumerate(["1", "2", "5", "9", "4"], 1)]
    judge = _judge([_row("swap list positions", "compose")],
                   [{"obligation_id": "ob1", "text": "done", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _, _, _, nf = repl.run_turn(
        "Swap the second and fifth on my numbers list.",
        EffectRunner(mode="record"), None, None,
        session_facts={"_chips": json.dumps(chips)})
    after = [c["value"] for c in json.loads(nf["_chips"]) if c["kind"] == "list"]
    assert after == ["1", "4", "5", "9", "2"], f"positional swap, chip untouched: {after}"
    field = [c for c in json.loads(nf["_chips"]) if c["kind"] == "field"]
    assert field and field[0]["value"] == "5"       # the chip value survives


def test_y2c_same_label_remint_rebinds_ops_to_the_new_list(monkeypatch):
    """'items' reminted with new values — a later delete acts on the NEW list."""
    old = [{"id": f"o{i}", "kind": "list", "label": "items", "attr": i,
            "value": v, "status": "live", "revision": 1}
           for i, v in enumerate(["red", "green", "blue"], 1)]
    judge = _judge([_row("create the new items list", "compose")],
                   [{"obligation_id": "ob1", "text": "1. one\n2. two\n3. three",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _, _, _, nf = repl.run_turn(
        "Replace my items list with: one, two, three.",
        EffectRunner(mode="record"), None, None,
        session_facts={"_chips": json.dumps(old)})
    judge2 = _judge([_row("delete from items", "compose")],
                    [{"obligation_id": "ob1", "text": "ok", "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge2)
    _, _, _, nf2 = repl.run_turn(
        "Delete the second item on my items list.",
        EffectRunner(mode="record"), None, None, session_facts=nf)
    after = [c["value"] for c in json.loads(nf2["_chips"]) if c["kind"] == "list"]
    assert "green" not in after and "red" not in after, f"old list must be gone: {after}"
    assert after == ["one", "three"], f"delete acts on the NEW list: {after}"


def test_ibe_no_web_veto_never_blocks_a_pure_time_derive(monkeypatch):
    """IB-E: NO-WEB plus a self-contained clock sum — the veto tears web, the
    typed derive still answers, zero fetches on the tape."""
    judge = _judge([_row("what time in 45 minutes", "arithmetic", "14:20 + 45 minutes")],
                   [{"obligation_id": "ob1", "text": "It will be 15:05.", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    runner = EffectRunner(mode="record")
    transcript, _, _, _ = repl.run_turn(
        "No web for this turn. It is 14:20 — what time will it be in 45 minutes?",
        runner, lambda q: (_ for _ in ()).throw(AssertionError("fetch fired under veto")), "test")
    ans = repl._extract_answer(transcript) or ""
    assert "15:05" in ans, f"the veto must not block a pure derive: {ans!r}"
    web_effects = [e for e in runner.journal.entries()
                   if isinstance(e, dict) and str(e.get("effect_id", "")).startswith("web.")]
    assert not web_effects, f"zero fetches under the veto: {web_effects}"
