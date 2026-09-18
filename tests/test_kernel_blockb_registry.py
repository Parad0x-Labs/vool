"""BLOCK-B seam: capability registry — refusal renders, op/ask stems, phantom cancel.

Signed block c31a...bd4b: "REFUSED-with-reason RENDERS the reason as a typed
sentence (silence != refusal); an op whose effect does not match the ask cannot
ship; cancel requires a live task — a cancel over nothing renders 'nothing was
running'."
"""
from core.kernel import repl
from core.kernel.effects import EffectRunner


def _judge(extract):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return extract
        raise AssertionError(f"unexpected model call {effect_id}")
    return fake


def test_capability_gap_refusal_is_user_visible(monkeypatch):
    """t69-71 class: 'if you cannot, SAY SO' must ship the limitation sentence."""
    judge = _judge({"obligations": [
        {"description": "read the OS clipboard", "lane": "machine",
         "query": "none:no clipboard surface in this REPL", "format": "",
         "source_offset": 0, "resolves_carryover": ""}]})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Read my clipboard. If you cannot, say that limitation plainly.",
        EffectRunner(mode="record"), None, None)
    answer = repl._extract_answer(transcript)
    assert answer is not None, "a refusal must SHIP an answer span, not just diagnostics"
    assert "capability gap" in answer and "clipboard" in answer   # rendered, not silent


def test_substitute_op_refused_by_ask_stems(monkeypatch):
    """t161 class: an inspection ask must never run str.reverse as a stand-in."""
    judge = _judge({"obligations": [
        {"description": "inspect the project layout", "lane": "machine",
         "query": "str.reverse:inspect the project layout", "format": "",
         "source_offset": 0, "resolves_carryover": ""}]})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "READ ONLY: inspect the project layout.", EffectRunner(mode="record"), None, None)
    answer = repl._extract_answer(transcript)
    assert answer is not None and "operation mismatch" in answer
    assert "tuoyal tcejorp" not in transcript       # the reversed text never ships


def test_phantom_cancel_says_nothing_was_running(monkeypatch):
    """A10/t91 class: a bare 'cancel the current task' answers, never silently
    mints a cancelled state."""
    judge = _judge({"obligations": [
        {"description": "cancel the current task", "lane": "cancelled", "query": "",
         "format": "", "source_offset": 0, "resolves_carryover": ""}]})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Cancel the current task.", EffectRunner(mode="record"), None, None)
    answer = repl._extract_answer(transcript)
    assert answer is not None and "nothing was running" in answer


def test_same_message_retraction_still_settles_normally(monkeypatch):
    """Negative control: a cancel that NAMES the retracted action is a retraction,
    not a phantom — the normal settle line stands and no 'nothing was running'."""
    judge = _judge({"obligations": [
        {"description": "look up the gold price (retracted)", "lane": "cancelled",
         "query": "", "format": "", "source_offset": 26, "resolves_carryover": ""},
        {"description": "greet", "lane": "chat", "query": "", "format": "",
         "source_offset": 44, "resolves_carryover": ""}]})
    monkeypatch.setattr(repl, "_model_json", judge)
    transcript, _, _, _ = repl.run_turn(
        "Look up the gold price. HOLD ON, cancel that, just say hi.",
        EffectRunner(mode="record"), None, None)
    assert "cancelled by the user later in the same message" in transcript
    assert "nothing was running" not in transcript
