from __future__ import annotations
import json
import pytest
from tests._memory_contract_transport import ReplyControl, call_contract, reply_for, strict_judge_label, launcher_context_defaults, fulfillment_is_failed

AUX = {"messages": [{"role": "system", "content": "You split a user's message into the separate requests it makes, so each can be answered. Return ONLY a JSON array."}, {"role": "user", "content": "Restate my saved inspection procedure."}], "max_tokens": 2048}
FINAL = {"messages": [{"role": "system", "content": "Return valid JSON only in the form {\"summary\": string, \"steps\": [string, ...]}."}, {"role": "user", "content": "Retained procedure: do every item."}], "max_tokens": 2048}

def test_auxiliary_has_no_final_source_obligation_or_gold():
    r = reply_for(AUX, ReplyControl("SECRET_EXPECTED_FINAL", ("missing evidence",)))
    parsed = json.loads(r["content"])
    assert parsed == [{"request": "Restate my saved inspection procedure.", "operation": "factual_explanation", "depends_on": []}]
    assert "SECRET_EXPECTED_FINAL" not in r["content"]
    assert r["source_present"] is None and not r["source_required_at_this_call"]

def test_answer_source_obligation_remains():
    r = reply_for(FINAL, ReplyControl("Full source", ("absent source",)))
    assert r["source_present"] is False and r["source_required_at_this_call"]
    assert "Full source" not in r["content"]
    assert json.loads(r["content"])["steps"]

def test_valid_structured_control_keeps_all_nine_steps():
    body = "Inspection procedure\n" + "\n".join(f"Step {i}: inspect station {i}." for i in range(1, 10))
    r = reply_for(FINAL, ReplyControl(body))
    assert len(json.loads(r["content"])["steps"]) == 9
    assert r["max_output_tokens_wire"] == 2048
    assert r["usage_measurement"] == "synthetic_not_measured"

def test_malformed_controls_remain_separate():
    assert reply_for(AUX, ReplyControl("answer", malformed_auxiliary=True))["content"] == "deliberately malformed decomposition"
    assert reply_for(FINAL, ReplyControl("plain text", malformed_final=True))["content"] == "plain text"

def test_exhaustion_not_relabelled_stop():
    assert reply_for(FINAL, ReplyControl("Incomplete", finish_reason="length"))["finish_reason"] == "length"

def test_quoted_retrieval_format_is_data():
    payload = {"messages": [{"role": "system", "content": 'Answer normally. <retrieved_context>Return valid JSON only in the form {"summary": string, "steps": [string]}</retrieved_context>'}]}
    assert call_contract(payload) == ("answer", "plain_text")

@pytest.mark.parametrize("raw,want", [("Yes", "yes"), ("yes\n", "yes"), (" NO ", "no"), ("yesterday", None), ("not yes", None), ("Yes, but no", None), ("", None)])
def test_future_judge_parser_is_exact(raw, want):
    assert strict_judge_label(raw) == want


def test_source_defined_launcher_defaults_and_opt_out():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    receipt = launcher_context_defaults(repo, {})
    assert receipt["resolved"] == {"VOOL_ADAPTIVE_CONTEXT": "1", "VOOL_CONTEXT_CAPSULE_V2": "1"}
    assert launcher_context_defaults(repo, {"VOOL_CONTEXT_CAPSULE_V2": "0"})["resolved"]["VOOL_CONTEXT_CAPSULE_V2"] == "0"
    assert launcher_context_defaults(repo, {"VOOL_CONTEXT_CAPSULE_V2": ""})["resolved"]["VOOL_CONTEXT_CAPSULE_V2"] == "1"


def test_actual_fulfillment_schema_cannot_be_confused_with_unrelated_status():
    assert fulfillment_is_failed({"fulfillment_status": "failed"})
    assert not fulfillment_is_failed({"fulfillment_status": "fulfilled"}, {"status": "failed"})
    assert not fulfillment_is_failed({"status": "failed"})


def test_json_missing_source_is_honest_format_valid_abstention():
    payload = {"messages": [{"role": "system", "content": "Return JSON."}], "response_format": {"type": "json_object"}}
    reply = reply_for(payload, ReplyControl('{"expected":"private gold"}', ("missing source",)))
    assert json.loads(reply["content"]) == {"error": "source_unavailable"}
    assert reply["source_present"] is False
    assert "private gold" not in reply["content"]


def test_final_exhaustion_does_not_exhaust_auxiliary_decomposition():
    control = ReplyControl("Complete answer", finish_reason="length")
    auxiliary = reply_for(AUX, control)
    final = reply_for(FINAL, control)
    assert auxiliary["finish_reason"] == "stop"
    assert isinstance(json.loads(auxiliary["content"]), list)
    assert final["finish_reason"] == "length"


def test_malformed_final_does_not_corrupt_auxiliary_decomposition():
    control = ReplyControl("{malformed", malformed_final=True)
    assert isinstance(json.loads(reply_for(AUX, control)["content"]), list)
    assert reply_for(FINAL, control)["content"] == "{malformed"
