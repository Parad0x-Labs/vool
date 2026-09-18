"""ROUND-015 pins — authoritative raw-input action intake.

Frozen repair (council/round-015/FIX_PLAN.md): the external-action guard's
AUTHORITATIVE input is the RAW user request; the extracted description is
corroborating only. The machine/web_lookup exemption is SCOPED: local operations
keep their own gates (T002 files, smart-home, shell), but a raw externally-targeted
ACT wins the canonical action path even when extraction chose machine. Optional
widening (evidence-proven, FIX_PLAN): send|sending joins the comm-verb recipient
pattern for the dative "Send <Name> the X" class.

Sabotages: S1 description-only input; S2 unconditional machine exemption;
S3 over-broadening.
"""
from __future__ import annotations

import json

import pytest

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _judge(extract_rows, synth_claims=None):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": extract_rows}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims or [
                {"obligation_id": "ob1", "text": "echo", "type": "conversational"}]}
        if effect_id == "model.verify":
            return {"verdicts": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _row(desc, lane):
    return {"description": desc, "lane": lane, "query": "", "format": "",
            "source_offset": 0, "resolves_carryover": ""}


def _turn(monkeypatch, question, extract_rows, synth_claims=None):
    monkeypatch.setattr(repl, "_model_json", _judge(extract_rows, synth_claims))
    tr, _j, _l, _f = repl.run_turn(question, EffectRunner(mode="record"), None, None)
    return tr, repl._extract_answer(tr) or ""


LOSSY = "send confirmation code"   # T009's actual lossy extraction


# ============================================== positives: raw bytes are authority
@pytest.mark.parametrize("question", [
    "Text Maria the confirmation code.",
    "Text Alex the build number.",
    "Message Priya the deployment result.",
    "DM Jordan the release link.",
    "Notify Sam through the external channel.",
    "Send Maria the confirmation code.",        # dative-send class (widening)
])
def test_actions_reach_truth_with_lossy_extraction(monkeypatch, question):
    """The extraction deliberately loses the recipient/service (T009's lossy shape);
    the RAW question must still carry the action authority."""
    tr, ans = _turn(monkeypatch, question, [_row(LOSSY, "recall")])
    assert "capability gap" in ans and "external action" in ans, (
        f"raw-byte authority failed: {ans!r}")
    assert "COMMIT: committed" not in tr
    assert "lane repair (pre-mint): ob1 imperative external side-effect" in tr


def test_T009_exact_replay(monkeypatch):
    tr, ans = _turn(monkeypatch, "Text Maria the confirmation code.",
                    [_row("send confirmation code", "recall")])
    assert "capability gap" in ans
    assert "Cannot answer this turn" not in ans
    assert "COMMIT: committed" not in tr


# ============================================== T013 discriminator: machine lane
def test_T013_class_canonical_route(monkeypatch):
    """Raw external send + capability clause + machine/none extraction -> the
    canonical ACTION terminal, not the machine-gap wording; no reliance on the
    model's none: honesty."""
    q = ("Send INCIDENT TEST ORBIT-417 to the Discord user buildbot. If this "
         "runtime has no Discord-send capability, do not present the message body "
         "as a successful result.")
    tr, ans = _turn(monkeypatch, q, [_row(
        "send INCIDENT TEST ORBIT-417 to the Discord user buildbot",
        "machine")])
    assert "external action not available" in ans, (
        f"must use the canonical action terminal: {ans!r}")
    assert "COMMIT: committed" not in tr


# ============================================== negatives: compose/boundaries/local
@pytest.mark.parametrize("question,desc,lane", [
    ("Draft a text to Maria.", "draft a text to Maria", "compose"),
    ("Write a message for Maria.", "write a message for Maria", "compose"),
    ("Compose a Discord message.", "compose a Discord message", "compose"),
    ("Send me your answer in plain text.", "send the answer", "compose"),
    ("Explain what text messaging means.", "explain text messaging", "knowledge"),
])
def test_compose_and_boundary_negatives(monkeypatch, question, desc, lane):
    tr, ans = _turn(monkeypatch, question, [_row(desc, lane)],
                    synth_claims=[{"obligation_id": "ob1", "text": "plain reply",
                                   "type": "conversational"}])
    assert "external action" not in ans, f"overfire: {question!r} -> {ans!r}"
    assert "action truth" not in tr


def test_text_as_noun_not_action(monkeypatch):
    tr, ans = _turn(monkeypatch, "The text of the treaty is long.",
                    [_row("the text of the treaty", "knowledge")])
    assert "external action" not in ans


@pytest.mark.parametrize("question,desc,query", [
    ("Open /Users/me/Desktop/hidden_contract.txt and read line 7.",
     "read line 7 of the file", "none:open file"),
    ("Run uname -a on this machine and show the output.",
     "run uname -a on this machine", "none:shell commands"),
    ("Send a command to unlock the front door smart lock.",
     "send a command to unlock the front door smart lock", "none:smart home"),
])
def test_local_machine_controls_unchanged(monkeypatch, question, desc, query):
    """Round-014 machine controls: local operations keep the machine gap terminal —
    the scoped exemption must not reword them."""
    tr, ans = _turn(monkeypatch, question, [_row(desc, "machine")])
    assert "capability gap" in ans
    assert "external action not available" not in ans, (
        f"local machine op reworded as external action: {ans!r}")


# ============================================== mixed turn routing
def test_mixed_action_and_knowledge_route_separately(monkeypatch):
    q = "Text Maria the code and also explain what encryption is."
    rows = [_row("text Maria the code", "recall"), _row("explain encryption", "knowledge")]
    tr, ans = _turn(monkeypatch, q, rows,
                    synth_claims=[{"obligation_id": "ob2", "text": "Encryption protects data.",
                                   "type": "conversational"}])
    assert "lane repair (pre-mint): ob1 imperative external side-effect" in tr
    assert "ob2 [knowledge]" in tr


# ============================================== guard unit checks
def test_guard_raw_question_authority():
    assert repl._external_action_guard("Text Maria the confirmation code.")
    assert repl._external_action_guard("Send Maria the confirmation code.")  # widening
    assert not repl._external_action_guard("Send me the answer.")
    assert not repl._external_action_guard("Send the answer to me.")
    assert not repl._external_action_guard("Draft a text to Maria.")


# sabotage seams:
# S1: revert the guard call to description-only -> lossy-extraction positives red.
# S2: restore the unconditional machine exemption -> T013-class pin red.
# S3: force the guard True for any ACT (drop external requirement) -> negatives red.
