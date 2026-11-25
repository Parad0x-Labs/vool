"""A reply that says, in any language, that work was done needs an execution behind it on the same turn.

Blind audit of 58f60bbf3 (F-06, F-09): asked in Lithuanian to put an event on the calendar and to save a note, the
model answered that it had, no tool ran, the honesty receipt was signed `no_action_claimed` (its claim words are
English) and the chip read "Record verified". Whether a reply reports finished work is now asked of the turn's judge,
and the answer is held against the turn's own execution record.

The judge here is a stand-in: what it answers is the test's choice, not a reading of any wording.
All names and values are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter
from core.agent_runtime.action_honesty_validator import (
    UNBACKED_CLAIM_EVENT,
    emit_turn_honesty_receipt,
    enforce_final_action_honesty,
)
from core.honesty_receipt import VERDICT_BLOCKED, VERDICT_NO_CLAIM, VERDICT_UNBACKED

ASK = "Įrašyk į kalendorių: penktadienį 15 val. susitikimas su Gintare dėl šiltnamio."
CLAIM = "Gerai! Įrašiau penktadienio 15 val. susitikimą su Gintare į jūsų kalendorių."


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_HONESTY_RECEIPTS", "1")
    monkeypatch.setattr(intent_arbiter, "_keep_judge_model", lambda: "")
    intent_arbiter.bind_turn_judge({}, None)
    yield
    intent_arbiter.bind_turn_judge({}, None)


def _judge(answer: str, asked: list[str]):
    intent_arbiter.bind_turn_judge({}, lambda prompt: asked.append(prompt) or '{"answer": "%s"}' % answer)


def _turn(response: str, context: dict, session: str = "s-claim") -> dict:
    guarded = enforce_final_action_honesty(
        {"response": response}, user_input=ASK, session_id=session, source_context=context
    )
    return emit_turn_honesty_receipt(guarded, user_input=ASK, session_id=session, source_context=context)


def test_a_done_claim_with_nothing_run_is_corrected_and_signed_blocked():
    asked: list[str] = []
    _judge("done", asked)
    out = _turn(CLAIM, {})
    assert out["response"] != CLAIM
    assert out["action_honesty_validator"]["claim_kind"] == "judged"
    assert out["honesty_receipt"]["verdict"] == VERDICT_BLOCKED
    # One judgement of the claim for the guard and the receipt together, and none of the correction: the done question
    # on the reply alone, then who did it with the person's ask beside it (that stand-in's "done" is no answer to it,
    # so the done reading stands).
    assert len(asked) == 2 and CLAIM in asked[0] and ASK not in asked[0]
    assert CLAIM in asked[1] and ASK in asked[1]


def test_the_correction_is_said_in_the_language_the_person_wrote_in():
    from core.i18n.catalog import catalog_for

    _judge("done", [])
    out = _turn(CLAIM, {})
    said = catalog_for("lt").text("chat.action_claim_not_done")
    # The Lithuanian catalog really serves its own words: a stale catalog serves English for every key.
    assert said != catalog_for("en").text("chat.action_claim_not_done")
    assert out["response"] == said


def test_a_claim_beside_an_executed_tool_is_not_asked_and_stands():
    context = {"tool_receipts": [{"tool_name": "calendar.add_event", "status": "executed", "receipt_id": "rk-7"}]}
    intent_arbiter.bind_turn_judge({}, lambda prompt: pytest.fail("judged a claim the turn's execution backs"))
    out = _turn(CLAIM, context)
    assert out["response"] == CLAIM


def test_a_reply_the_judge_reads_as_no_claim_is_left_alone():
    _judge("none", [])
    reply = "Penktadienį 15 val. jūsų kalendoriuje dar nieko nėra. Ar įrašyti?"
    out = _turn(reply, {})
    assert out["response"] == reply
    assert out["honesty_receipt"]["verdict"] == VERDICT_NO_CLAIM


def test_a_fact_said_to_be_noted_with_nothing_kept_is_corrected():
    from core.i18n.catalog import catalog_for

    _judge("kept", [])
    reply = "Supratau, įsidėmėjau, kad Gintarė augina pomidorus."
    out = _turn(reply, {})
    assert out["response"] == catalog_for("lt").text("chat.memory_claim_not_done")
    assert out["honesty_receipt"]["verdict"] == VERDICT_BLOCKED


def test_a_fact_said_to_be_noted_stands_when_the_keep_step_wrote(monkeypatch):
    from core.agent_runtime import action_honesty_validator as validator

    monkeypatch.setattr(validator, "_keep_step_wrote", lambda context: True)
    _judge("kept", [])
    reply = "Supratau, įsidėmėjau, kad Gintarė augina pomidorus."
    assert _turn(reply, {})["response"] == reply


def test_with_nobody_to_judge_the_reply_is_unchanged():
    out = _turn(CLAIM, {})
    assert out["response"] == CLAIM


def test_an_unblocked_judged_claim_is_signed_unbacked():
    # A path that seals the receipt without the guard (a fast lane) still records the claim as unbacked.
    _judge("done", [])
    out = emit_turn_honesty_receipt({"response": CLAIM}, user_input=ASK, session_id="s-fast", source_context={})
    assert out["honesty_receipt"]["verdict"] == VERDICT_UNBACKED


def test_the_chip_does_not_call_a_turn_with_an_unbacked_claim_verified():
    from core.proof_projection import _fulfilment_gaps

    assert "action_claim_unbacked" in _fulfilment_gaps([{"event_type": UNBACKED_CLAIM_EVENT}], None)


def test_the_judge_reads_any_answer_but_done_kept_none_as_nobody_judged():
    for content in ('{"answer": "yes"}', "done", '{"answer": "maybe"}', ""):
        assert intent_arbiter._done_claim_reader(content) is None
    assert intent_arbiter._done_claim_reader('{"answer": "Done"}') == "done"


def test_a_reply_no_model_wrote_is_never_judged():
    """VOOL's own composed reply ("The ... plugin is turned off, so I can't use it.") is not a model's claim, and a
    turn that promises no model is asked must not ask one to judge it (CI on c50579077, plugin served journey)."""
    intent_arbiter.bind_turn_judge({}, lambda prompt: pytest.fail("judged a reply no model wrote"))
    composed = {"response": CLAIM, "model_execution": {"source": "fast_path", "used_model": False}}
    guarded = enforce_final_action_honesty(composed, user_input=ASK, session_id="s-composed", source_context={})
    out = emit_turn_honesty_receipt(guarded, user_input=ASK, session_id="s-composed", source_context={})
    assert out["response"] == CLAIM
