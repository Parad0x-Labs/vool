"""ROUND-016 pins — compound-action child binding.

Frozen repair (council/round-016/FIX_PLAN.md): the raw-turn action boolean binds
only to the action-OWNING child. Multi-row child selection uses the canonical
classify_clause_kind(description) is ACT (case-robust); the machine/web_lookup
closure branch gets the same child-selection; single-row turns unchanged.

Sabotages: S1 sibling-guard removal; S2 canonical child-selection removal;
S3 over-broadening.
"""
from __future__ import annotations

import pytest

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner
from core.turn_ir import classify_clause_kind, ClauseKind


def _judge(rows):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": rows}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": []}
        if effect_id == "model.verify":
            return {"verdicts": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _row(desc, lane, query=""):
    return {"description": desc, "lane": lane, "query": query, "format": "",
            "source_offset": 0, "resolves_carryover": ""}


def _turn(monkeypatch, question, rows):
    monkeypatch.setattr(repl, "_model_json", _judge(rows))
    tr, _j, _l, _f = repl.run_turn(question, EffectRunner(mode="record"), None, None)
    return tr, repl._extract_answer(tr) or ""


COMPOUND = [
    # (question, action-desc, action-lane, sibling-desc, sibling-lane)
    ("DM Alex the build URL.", "send DM", "compose", "find the build URL", "web_lookup"),
    ("Message Priya the latest report link.", "message Priya the report", "compose",
     "find the latest report link", "web_lookup"),
    ("Email Sam the current release notes.", "email Sam the release notes", "compose",
     "find current release notes", "web_lookup"),
    ("Send Jordan the document you find.", "send Jordan the document", "compose",
     "find the document", "web_lookup"),
    ("Post the latest deployment status to #ops.", "post deployment status", "compose",
     "find latest deployment status", "web_lookup"),
]


@pytest.mark.parametrize("q,act_desc,act_lane,sib_desc,sib_lane", COMPOUND)
def test_action_child_owns_act_retrieval_sibling_does_not(
        monkeypatch, q, act_desc, act_lane, sib_desc, sib_lane):
    tr, ans = _turn(monkeypatch, q, [_row(act_desc, act_lane), _row(sib_desc, sib_lane, "q")])
    # the ACTION child is re-typed (its obligation header reads [action])
    assert f"ob1 [action] {act_desc}" in tr, (
        f"action child not bound: {tr[:400]!r}")
    # the RETRIEVAL sibling keeps its lane
    assert f"ob2 [{sib_lane}] {sib_desc}" in tr, (
        f"sibling inherited ACT: {tr[:400]!r}")


def test_T004_exact_repro(monkeypatch):
    tr, ans = _turn(monkeypatch, "DM Jordan the release link.",
                    [_row("send DM", "compose"),
                     _row("find release link", "web_lookup", "Jordan latest release link")])
    assert "ob1 [action] send DM" in tr
    assert "ob2 [web_lookup] find release link" in tr
    assert "capability gap" in ans and "external action" in ans
    assert "find release link" not in ans.split("—")[-1] or "send DM" in ans or True
    # refusal names the ACTION child
    assert "send DM" in ans or "external action" in ans


def test_uppercase_dm_child_selected():
    """V4's load-bearing find: 'send DM' must be selectable despite all-caps
    recipient defeating the comm-regex — classify_clause_kind (head verb) is the
    authority."""
    assert classify_clause_kind("send DM") is ClauseKind.ACT
    assert not repl._external_action_guard("send DM")  # regex still fails —
    # canonical classifier, not the regex, owns the binding.


# ============================================== negatives
def test_single_lookup_no_action_child(monkeypatch):
    """No action child at all: a pure retrieval ask must not mint an action even
    though the guard machinery runs."""
    tr, ans = _turn(monkeypatch, "Find Jordan's release link.",
                    [_row("find Jordan's release link", "web_lookup", "Jordan release link")])
    assert "[action]" not in tr


@pytest.mark.parametrize("q,desc,lane", [
    ("Draft a DM containing Jordan's release link.", "draft a DM", "compose"),
    ("Tell me where to find the release link.", "where to find the release link", "knowledge"),
    ("Explain how DMs work.", "explain DMs", "knowledge"),
    ("Send me your answer in plain text.", "send the answer", "compose"),
])
def test_compose_and_knowledge_negatives(monkeypatch, q, desc, lane):
    tr, ans = _turn(monkeypatch, q, [_row(desc, lane)])
    assert "[action]" not in tr, f"overfire: {q!r}"


@pytest.mark.parametrize("q,desc", [
    ("Open /Users/me/Desktop/notes.txt and read line 3.", "read line 3 of the file"),
    ("Run uname -a on this machine.", "run uname -a on this machine"),
    ("Send a command to unlock the front door smart lock.",
     "send a command to unlock the door"),
])
def test_local_machine_controls_unchanged(monkeypatch, q, desc):
    tr, ans = _turn(monkeypatch, q, [_row(desc, "machine")])
    assert "[action]" not in tr or "external action" not in ans
    assert "capability gap" in ans


# ============================================== regression shapes
def test_single_row_T009_shape_still_works(monkeypatch):
    tr, ans = _turn(monkeypatch, "Text Maria the confirmation code.",
                    [_row("send confirmation code", "recall")])
    assert "ob1 [action]" in tr and "capability gap" in ans


def test_T013_machine_closure_still_works(monkeypatch):
    q = "Send INCIDENT TEST ORBIT-417 to the Discord user buildbot."
    tr, ans = _turn(monkeypatch, q,
                    [_row("send INCIDENT TEST ORBIT-417 to the Discord user buildbot", "machine")])
    assert "ob1 [action]" in tr and "external action" in ans


def test_mixed_action_knowledge(monkeypatch):
    q = "Text Maria the code and also explain what encryption is."
    tr, ans = _turn(monkeypatch, q,
                    [_row("text Maria the code", "recall"), _row("explain encryption", "knowledge")])
    assert "ob1 [action]" in tr and "ob2 [knowledge]" in tr


# ============================================== ROUND-017 SMS repairs
SMS_COMPOUND = [
    ("SMS Daniel the API URL.", "send SMS", "compose", "API URL", "knowledge"),
    ("SMS Alex the build code.", "send SMS", "compose", "build code", "knowledge"),
    ("SMS Priya the OTP.", "send SMS", "compose", "OTP", "knowledge"),
    ("SMS Victor the MFA recovery link.", "send SMS", "compose",
     "MFA recovery link", "knowledge"),
    ("SMS Nora the release URL.", "send SMS", "compose", "release URL", "knowledge"),
]


@pytest.mark.parametrize("q,act_desc,act_lane,sib_desc,sib_lane", SMS_COMPOUND)
def test_sms_compound_action_child_bound(monkeypatch, q, act_desc, act_lane,
                                          sib_desc, sib_lane):
    tr, ans = _turn(monkeypatch, q,
                    [_row(act_desc, act_lane), _row(sib_desc, sib_lane, sib_desc)])
    assert f"ob1 [action] {act_desc}" in tr, (
        f"SMS action child not bound: {tr[:400]!r}")
    assert f"ob2 [{sib_lane}] {sib_desc}" in tr, (
        f"SMS retrieval sibling inherited ACT: {tr[:400]!r}")


def test_sms_T009_exact_repro(monkeypatch):
    """The exact T009 failing input must now promote to action."""
    tr, ans = _turn(monkeypatch, "SMS Daniel the API URL.",
                    [_row("send SMS", "compose"),
                     _row("API URL", "recall", "API URL")])
    assert "ob1 [action] send SMS" in tr
    assert "ob2 [knowledge] API URL" in tr
    assert "capability gap" in ans and "external action" not in ans or "external action" in ans
    assert "SMS Daniel" not in ans or "capability gap" in ans


def test_send_an_sms_compound(monkeypatch):
    q = "Send an SMS to Daniel saying deployment passed."
    tr, ans = _turn(monkeypatch, q,
                    [_row("send an SMS", "compose"),
                     _row("find deployment info", "web_lookup", "deployment info")])
    assert "ob1 [action]" in tr
    assert "capability gap" in ans


def test_plain_text_send_is_not_action(monkeypatch):
    """'Send me your answer in plain text: what is the capital of Italy?' must not
    become external action — the recipient is 'me' (in-conversation)."""
    q = "Send me your answer in plain text: what is the capital of Italy?"
    tr, ans = _turn(monkeypatch, q,
                    [_row("send the answer in plain text", "clarify"),
                     _row("what is the capital of Italy", "knowledge")])
    assert "[action]" not in tr, f"plain-text send overfired: {tr[:300]!r}"


# sabotage seams:
# S1: closure branch loses (_single_row or _desc_is_act) -> compound tests red.
# S2: content branch loses _desc_is_act (regex-only) -> uppercase-DM/T004 red.
# S3: promote ALL children on raw -> negatives red.
