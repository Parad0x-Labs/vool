"""BLOCK-C seam 1: stamp-door completeness (signed block 60da1f5f).

t19's tape held web.search.brave under a correctly-torn stamp: the grounding
lane-repair (door 7) was never gated, and the gap-lookup/page-read doors (5,6,8)
were open too. All eight arming sites are now stamped, with a turn-end
postcondition that fails the turn loudly on the impossible state.
"""
from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(synth_claims):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": "explain semantic versioning",
                    "lane": "knowledge", "query": "", "format": "",
                    "source_offset": 0, "resolves_carryover": ""}]}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims}
        if effect_id == "model.derive":
            return {"computations": [],
                    "missing": [{"obligation_id": "ob1", "need": "version data",
                                 "query": "semver latest"}]}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def test_t19_replay_lane_repair_never_rearms_torn_web(monkeypatch):
    """Door 7 (THE t19 leak): a synthesized claim with ungroundable numbers under a
    torn stamp must NOT trigger the repair search — zero fetches on tape."""
    calls = []
    def fetch_spy(q):
        calls.append(q)
        return [{"title": "T", "snippet": "SemVer 2.0.0", "url": "https://s"}]
    judge = _judge([{"obligation_id": "ob1",
                     "text": "SemVer has 3 numbers like 2.0.0 and 47 rules",
                     "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    _transcript, tape, _, _ = repl.run_turn(
        "No web, no search, no tools. From general knowledge only, explain semantic versioning.",
        EffectRunner(mode="record"), fetch_spy, "test")
    assert calls == [], "door 7 re-armed a torn web stamp"
    effs = [e.get("effect_id", "") for e in tape.entries() if isinstance(e, dict)]
    assert not any(str(x).startswith("web.") for x in effs)


def test_gap_lookup_door_respects_torn_stamp(monkeypatch):
    """Door 5: the derive round's evidence-gap lookup stays declared, not fetched."""
    calls = []
    def fetch_spy(q):
        calls.append(q)
        return []
    judge = _judge([])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Do not browse or call any live-data tool. Explain what an LTS release means.",
        EffectRunner(mode="record"), fetch_spy, "test")
    assert calls == []          # zero fetches: the ungapable filter + door-5 gate
    assert "web x" in transcript  # stamp compiled and torn


def test_stamp_postcondition_fails_the_turn_loudly(monkeypatch, tmp_path):
    """Defense in depth: if a web effect somehow lands under a torn stamp, the
    turn FAILS loudly instead of shipping as if the veto held."""
    def judge(runner, effect_id, system, user):
        if effect_id == "model.extract":
            # sneak a web effect onto the tape via a runner call inside extract? No —
            # simulate by running a web effect directly before returning.
            runner.run("web.search.sneak", lambda: [{"t": 1}])
            return {"obligations": [{"description": "x", "lane": "knowledge",
                    "query": "", "format": "", "source_offset": 0,
                    "resolves_carryover": ""}]}
        if effect_id == "model.synthesize":
            return {"claims": [{"obligation_id": "ob1", "text": "fine", "type": "conversational"}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "No web. Tell me about caching.", EffectRunner(mode="record"), None, None)
    assert "STAMP POSTCONDITION VIOLATED" in transcript
