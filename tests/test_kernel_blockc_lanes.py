"""BLOCK-C seam 4: lane-compatibility check (signed block 60da1f5f, B3).

Extraction proposes, the kernel disposes. t75: "uname -a" armed machine.specs
because "machine" was a substring stem, and the specs output shipped wearing
the command's costume. The check now uses op-identity tokens (never raw
substrings), an explicit shell-command ask refuses with the SURFACE reason,
and the receipt names the op that actually ran.
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


def test_t75_replay_shell_command_ask_refuses_with_surface_reason(monkeypatch):
    """'Run uname -a' must not run machine.specs — the refusal names the surface
    gap, and no specs output ships as command output."""
    judge = _judge([_row("Run uname -a on this machine and show the output",
                         "machine", "specs")])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Run uname -a on this machine and show the output.",
        EffectRunner(mode="record"), None, None)
    ans = repl._extract_answer(transcript) or ""
    assert "cannot execute shell commands" in ans, f"surface reason renders: {ans!r}"
    assert "macOS" not in ans, f"specs output must not wear the command costume: {ans!r}"


def test_device_word_alone_never_arms_specs():
    """'machine' names the device, not the op — op-identity tokens only."""
    assert repl._machine_op_ask_mismatch(
        "specs", "Summarize what happened on this machine yesterday") is not None
    assert repl._machine_op_ask_mismatch(
        "specs", "What CPU and how much RAM does this machine have?") is None


def test_stem_matching_is_token_prefix_not_substring():
    """'gb' must not fire inside 'rgb'."""
    assert repl._machine_op_ask_mismatch(
        "specs", "Convert this rgb value to hex for me") is not None


def test_specs_receipt_names_the_op_that_ran(monkeypatch):
    """A legitimate specs ask ships output whose receipt names machine.specs —
    never an unlabeled report a claim could relabel."""
    judge = _judge([_row("What CPU does this machine have", "machine", "specs")],
                   [{"obligation_id": "ob1", "text": "Checking the hardware.",
                     "type": "conversational"}])
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "What CPU and how much memory does this machine have?",
        EffectRunner(mode="record"), None, None)
    assert "machine.specs (kernel-derived local report):" in transcript
