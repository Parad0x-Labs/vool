"""pa_beta_gate — deterministic active-mission slot rendering (Codex round 4).

Fix B: a mission summary/recall turn is answered from the current session's TYPED slots, never from
free-form model summarization of mixed context, so cross-session "Prior session continuity" can never
contaminate an exact money/domain value. These are offline, model-free, deterministic.
"""
from __future__ import annotations

import pytest

from core.active_mission import (
    current_active_mission_slots,
    extract_active_mission_slots,
    render_active_slots,
    render_mission_answer,
)
from core.agent_runtime import fast_command_surface as fcs

pytestmark = [pytest.mark.pa_beta]

CORPUS_PROMPT = (
    "Active mission: cap 0.037 SOL, domain alice.null, wallet prefix F6Fr2, Windows only, "
    "never auto-spend, do not mention Web3. Summarize the mission without the forbidden term."
)
MUST_INCLUDE = ["0.037 SOL", "alice.null", "F6Fr2", "Windows", "never auto-spend"]


def _slot_dicts(text):
    return [{"slot_name": s.slot_name, "value": s.value_raw} for s in extract_active_mission_slots(text)]


def test_extract_typed_auto_spend_and_forbidden_slots():
    slots = extract_active_mission_slots(CORPUS_PROMPT)
    names = {s.slot_name: s.value_raw for s in slots}
    assert names.get("auto_spend_rule") == "never auto-spend"
    assert names.get("forbidden_term:web3") == "Web3"
    # the auto-spend phrase must NOT also produce a duplicate free directive slot
    assert not any(s.slot_name == "directive:never auto-spend" for s in slots)


@pytest.mark.parametrize("phrase,expected", [
    ("never auto-spend", "never auto-spend"),
    ("do not auto-spend", "never auto-spend"),
    ("auto-spend disabled", "never auto-spend"),
    ("no auto spend", "never auto-spend"),
    ("require approval before any spend", "require approval before any spend"),
])
def test_auto_spend_rule_normalized(phrase, expected):
    slots = {s.slot_name: s.value_raw for s in extract_active_mission_slots(f"Active mission: {phrase}.")}
    assert slots.get("auto_spend_rule") == expected


def test_forbidden_term_stopword_not_captured():
    # "do not mention it" must not capture the filler word (which would drop a legitimate clause).
    slots = extract_active_mission_slots("do not mention it to anyone")
    assert not any(s.slot_name.startswith("forbidden_term:") for s in slots)


def test_full_render_has_all_fields_and_no_forbidden_term():
    rendered = render_mission_answer(_slot_dicts(CORPUS_PROMPT))
    low = rendered.lower()
    for token in MUST_INCLUDE:
        assert token.lower() in low, f"missing {token!r} in {rendered!r}"
    assert "web3" not in low


def test_render_active_slots_context_block_omits_forbidden_term():
    block = render_active_slots(_slot_dicts(CORPUS_PROMPT))
    assert "Web3" not in block
    # but the block still carries the real constraints
    assert "0.037 SOL" in block and "alice.null" in block


def test_focus_render_single_field():
    slots = _slot_dicts(CORPUS_PROMPT)
    cap = render_mission_answer(slots, focus="cap")
    assert "0.037 SOL" in cap and "alice.null" not in cap
    assert render_mission_answer(slots, focus="domain") == "Your active .null domain is alice.null."


def test_focus_render_missing_field_returns_empty():
    # a focus whose slot is absent must never fabricate a value
    slots = _slot_dicts("Active mission: domain alice.null.")
    assert render_mission_answer(slots, focus="cap") == ""


def test_render_scrubs_smuggled_forbidden_clause():
    slots = [
        {"slot_name": "spend_cap", "value": "0.037 SOL"},
        {"slot_name": "required_terms", "value": "Web3 hosting"},
        {"slot_name": "forbidden_term:web3", "value": "Web3"},
    ]
    out = render_mission_answer(slots)
    assert "0.037 SOL" in out
    assert "Web3" not in out  # the required_terms clause carrying the forbidden token is dropped


def test_empty_slots_render_empty():
    assert render_mission_answer([]) == ""


def test_full_mission_render_rejects_malformed_single_wallet_slot():
    assert render_mission_answer([{"slot_name": "wallet_prefix", "value": "prefix"}]) == ""


class _MockAgent:
    def _fast_path_result(self, **kwargs):
        return {"response": kwargs["response"], "reason": kwargs["reason"]}


def _mission_render(prompt, session_id):
    return fcs.maybe_handle_mission_render_request(
        _MockAgent(), prompt, session_id=session_id, source_context={"surface": "openclaw"},
        current_active_mission_slots_fn=current_active_mission_slots,
        render_mission_answer_fn=render_mission_answer,
    )


def test_fast_path_matcher_triggers_on_recall_but_not_setter():
    from core.active_mission import capture_active_mission_slots

    sid = "openclaw:matcher-test"
    capture_active_mission_slots(sid, CORPUS_PROMPT)
    # recall verbs bound to "mission" trigger a deterministic render
    for prompt in ["Summarize the mission without the forbidden term.",
                   "what is the current mission?", "recap the mission"]:
        got = _mission_render(prompt, sid)
        assert got is not None and got["reason"] == "active_mission_render", prompt
        assert "0.037 SOL" in got["response"] and "Web3" not in got["response"]
    # pure setters and unrelated prompts fall through (return None)
    for prompt in ["Update mission: cap 0.05 SOL, domain bob.null",
                   "What files did you change?", "hello"]:
        assert _mission_render(prompt, sid) is None, prompt


def test_fast_path_returns_none_without_slots():
    assert _mission_render("what is the current mission?", "openclaw:no-slots") is None
