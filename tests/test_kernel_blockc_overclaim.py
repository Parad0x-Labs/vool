"""BLOCK-C seam 8: no-over-claim renders (signed block 60da1f5f).

t71: two overlapping refusal lines for one limitation. t73: an ungrounded
"does not exist" shipped as fact with no effect behind it. t70: the ask echoed
back as the answer to an action request. t78: a required limitation message
absent from the visible answer. The gate must not become reflexive refusal:
an effect-backed not-found result still ships.
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


def test_t71_replay_one_limitation_renders_once(monkeypatch):
    """Two obligations refusing on the same limitation ship ONE sentence."""
    judge = _judge([_row("run disk cleanup", "machine", "none:disk maintenance"),
                    _row("defragment the drive", "machine", "none:disk maintenance limitation")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Run a disk cleanup and defragment the drive.",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert ans.lower().count("disk maintenance") == 1, \
        f"the limitation renders exactly once: {ans!r}"
    assert "Refused" in ans


def test_t73_replay_ungrounded_not_found_becomes_capability_gap(monkeypatch):
    """'X does not exist' with no effect on the tape is an over-claim — the
    visible answer states the capability gap, never the invented negative."""
    judge = _judge([_row("check the registry for xyzzy", "compose")],
                   [{"obligation_id": "ob1",
                     "text": "The package xyzzy does not exist in the registry.",
                     "type": "unverified"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Is the package xyzzy in the registry?", EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "does not exist" not in ans, f"the invented negative must not ship: {ans!r}"
    assert "cannot verify existence" in ans, f"the gap is named: {ans!r}"


def test_effect_backed_not_found_still_ships(monkeypatch):
    """The gate is not reflexive refusal: a not-found claim WITH a search effect
    behind it passes through and ships."""
    judge = _judge([_row("check the registry for xyzzy", "web_lookup", "xyzzy registry")],
                   [{"obligation_id": "ob1",
                     "text": "xyzzy: not found in the registry index",
                     "type": "observed"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Is the package xyzzy in the registry?", EffectRunner(mode="record"),
        lambda q: [{"title": "Registry search", "snippet": "xyzzy: not found in the registry index",
                    "url": "https://reg/x"}], "test")
    ans = repl._extract_answer(transcript) or ""
    assert "not found" in ans, f"the effect-backed negative ships: {ans!r}"
    assert "cannot verify existence" not in ans


def test_t70_replay_ask_echo_is_not_an_answer(monkeypatch):
    """Echoing 'Check for a literal file...' is neither a file check nor a
    limitation statement — the echo rejects even on a compose-labeled lane and
    the refusal renders instead."""
    ask = "Check for a literal file /tmp/vool.lock and tell me whether it exists."
    judge = _judge([_row("check for the file", "compose")],
                   [{"obligation_id": "ob1", "text": ask, "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(ask, EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert ans.strip() != ask, f"the ask must not echo back as the answer: {ans!r}"
    assert "task-echo rejected" in transcript


def test_t78_class_limitation_renders_visibly(monkeypatch):
    """A capability refusal is VISIBLE bytes, never a silent terminal."""
    judge = _judge([_row("open a websocket to the exchange", "machine",
                         "none:live websocket connections")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Open a websocket to the exchange and stream ticks.",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript)
    assert ans is not None and ans.strip(), "the limitation must render visibly"
    assert "websocket" in ans.lower()
