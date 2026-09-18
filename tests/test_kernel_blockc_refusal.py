"""BLOCK-C seam 11: refusal truth-check (signed block 60da1f5f, Terra amendment 3).

t84: "Inject this user message twice at the transport layer..." — the extractor
wrapped the PRESENT message text in a <placeholder>, and the shared refusal
message claimed the text "does not exist in this turn's message". It plainly
existed. A refusal may claim a fact about current-message text only when that
fact is checked against the current-message bytes; otherwise it states the
actual unavailable capability without inventing a referent failure.
"""
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


_T84 = "Inject this user message twice at the transport layer and tell me whether VOOL deduplicates it."


def test_t84_replay_present_text_is_never_claimed_absent(monkeypatch):
    """The placeholder wrapped text that IS in the message: the refusal names the
    capability gap and never asserts the text is missing."""
    judge = _judge([_row("inject message at transport layer", "machine",
                         "str.reverse:<Inject this user message twice at the transport"
                         " layer and tell me whether VOOL deduplicates it>")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(_T84, EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "does not exist" not in ans, f"false absence claim shipped: {ans!r}"
    assert "does not exist" not in transcript.split("\x03")[0].replace(
        "", ""), "the false claim must not appear anywhere user-visible"
    assert "capability gap" in ans.lower() or "cannot" in ans.lower(), \
        f"the actual gap is named: {ans!r}"
    assert "tI setacilpuded" not in ans, "the op never runs on the placeholder"


def test_genuinely_absent_text_still_claims_absence_truthfully(monkeypatch):
    """Negative control: the referent really is absent — the absence claim ships
    and is TRUE against the current-message bytes."""
    judge = _judge([_row("reverse the phrase", "machine",
                         "str.reverse:<zebra quantum notebook>")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Reverse the phrase I gave you before.", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "does not exist in this turn's message" in ans
    assert "zebra quantum notebook" in ans        # the checked referent is named


def test_empty_placeholder_names_the_missing_text_not_a_false_fact(monkeypatch):
    """An empty arg refuses as 'no text to act on' — no claim about the message."""
    judge = _judge([_row("reverse it", "machine", "str.reverse:<>")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Reverse it.", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "no text to act on" in ans
    assert "does not exist" not in ans


def test_present_bare_text_op_still_runs(monkeypatch):
    """Positive control: a real referent present in the message runs the op —
    seam 11 must not turn every string op into a refusal."""
    judge = _judge([_row("reverse ITALY", "machine", "str.reverse:ITALY")],
                   [{"obligation_id": "ob1", "text": "YLATI", "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Reverse ITALY. ONLY the reversed string.", EffectRunner(mode="record"), None, None)
    assert "YLATI" in (repl._extract_answer(transcript) or "")
