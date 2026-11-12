"""A reply the runtime sets aside names the check that refused it, in codes only.

B5 (benchmark run 5, 2026-10-15): turns answered "I couldn't get a usable model response in this
run ..." while the same provider answered the turns around them, and no record named which check
had refused the provider's reply. The receipt added for that is proven here on the served daemon:
a real OpenAI-compatible request/response through the production router, validation and
publication, with only the model substituted by a synthetic local service (nothing is paid).
What this does not prove: the cause of the original B5 turns, which is still unknown.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from core.agent_runtime.reply_rejection import reply_rejection_receipt
from core.memory_first_router import ModelExecutionDecision
from tests.test_conversation_intent_served import (  # openrouter: the module-scoped served daemon
    _answer_text,
    _or_certification,
    _or_completion,
    _session,
    openrouter,
)

PRIVATE_MARKER = "KESTREL-PRIVATE-7781"
LEAKED_CALL = '<tool_call>{"name": "web_search", "arguments": {"query": "' + PRIVATE_MARKER + '"}}</tool_call>'
QUESTION = "Which of my two kiln schedules would finish first, the slow glaze or the bisque?"


def _audit_rejections(home) -> list[dict]:
    rows = []
    for db in home.rglob("*.db"):
        try:
            with sqlite3.connect(db) as conn:
                found = conn.execute(
                    "SELECT details_json FROM audit_log WHERE event_type = 'agent_run_once_complete'"
                ).fetchall()
        except sqlite3.Error:
            continue
        rows.extend(json.loads(row[0]) for row in found)
    return [row["reply_rejection"] for row in rows if "reply_rejection" in row]


def _task_failed_rejections(events: list[dict]) -> list[dict]:
    return [event["reply_rejection"] for event in events if event.get("event_type") == "task_failed" and "reply_rejection" in event]


def test_a_refused_provider_reply_names_its_check_without_its_text(openrouter) -> None:
    daemon, service = openrouter.daemon, openrouter.service
    # The provider answers HTTP 200 with a reply that is only a leaked foreign tool call.
    service.reply = lambda body: _or_certification(body) or _or_completion(body, content=LEAKED_CALL, completion_tokens=40)
    session = _session(f"rejected-{uuid.uuid4()}")
    before = len(service.inference_requests())
    status, answer = daemon.chat(QUESTION, session_id=session)
    events = daemon.events(session)
    text = _answer_text(answer)
    assert status == 200 and len(service.inference_requests()) - before == 1
    # This is one way a healthy provider produces the B5 notice.
    assert text.startswith("I couldn't get a usable model response in this run"), text
    assert PRIVATE_MARKER not in text

    receipts = _task_failed_rejections(events)
    assert len(receipts) == 1, [event.get("event_type") for event in events]
    receipt = receipts[0]
    assert receipt["schema"] == "vool.reply_rejection.v1"
    assert receipt["rejecting_check"] == "output_contract"
    assert receipt["branch"] == "output_contract_failed_refused"
    assert receipt["execution_source"] == "provider_execution"
    assert receipt["validation_state"] == "contract_failed"
    assert receipt["used_model"] is True
    assert receipt["contract_error"] and ":" not in receipt["contract_error"]
    assert PRIVATE_MARKER.lower() not in json.dumps(receipt).lower()
    # Durable: the same receipt is in the run audit stored in the daemon's home.
    assert receipt in _audit_rejections(daemon.home)


def test_an_accepted_reply_carries_no_rejection_receipt(openrouter) -> None:
    daemon, service = openrouter.daemon, openrouter.service
    reply = "The bisque finishes first; the slow glaze firing holds longer at peak."
    service.reply = lambda body: _or_certification(body) or _or_completion(body, content=reply)
    session = _session(f"accepted-{uuid.uuid4()}")
    status, answer = daemon.chat(QUESTION, session_id=session)
    assert status == 200 and _answer_text(answer) == reply
    assert _task_failed_rejections(daemon.events(session)) == []
    assert "reply_rejection" not in json.dumps(daemon.events(session))


def _decision(**overrides) -> ModelExecutionDecision:
    fields = {"source": "provider_execution", "task_hash": "t", "used_model": True, "validation_state": "valid", "output_text": ""}
    fields.update(overrides)
    return ModelExecutionDecision(**fields)


def test_parser_detail_and_reply_text_never_enter_the_receipt() -> None:
    decision = _decision(
        validation_state="contract_failed",
        output_text="my landlord Ms Q. Varga owes " + PRIVATE_MARKER,
        details={"contract_error": "invalid_json:Expecting value: line 1 column 1 (char 0) " + PRIVATE_MARKER, "warnings": ["foreign_tool_markers_stripped"]},
    )
    receipt = reply_rejection_receipt(decision, rendered_via="output_contract_failed_refused", response_reason="model_output_contract_failed")
    assert receipt["contract_error"] == "invalid_json"
    assert receipt["output_chars"] == len(decision.output_text)
    assert "varga" not in json.dumps(receipt).lower() and PRIVATE_MARKER.lower() not in json.dumps(receipt).lower()


def test_a_block_reason_keeps_only_its_leading_code() -> None:
    decision = _decision(source="selected_model_blocked", used_model=False, details={"reason": "explicit_heavy_lane_failed: upstream said " + PRIVATE_MARKER})
    receipt = reply_rejection_receipt(decision, rendered_via="honest_degraded_chat", response_reason="x")
    assert receipt["block_reason"] == "explicit_heavy_lane_failed"


def test_missing_keys_keep_only_schema_key_names() -> None:
    decision = _decision(validation_state="contract_failed", details={"contract_error": "missing_keys:summary,steps"})
    receipt = reply_rejection_receipt(decision, rendered_via="output_contract_failed_refused", response_reason="x")
    assert receipt["contract_error"] == "missing_keys:summary,steps"


def test_each_refusal_is_named_and_an_unexplained_one_stays_unknown() -> None:
    cases = [
        (_decision(source="memory_hit", used_model=False), "honest_degraded_chat", "cache_or_memory_source"),
        (_decision(output_text="{}"), "internal_payload_refused", "internal_payload"),
        (_decision(output_text="part one"), "multi_part_incomplete_refused", "missing_requested_parts"),
        (_decision(source="no_provider_available", used_model=False), "honest_degraded_chat", "no_model_reply"),
        (_decision(), "honest_degraded_chat", "empty_model_output"),
        (_decision(output_text="a reply that passed validation"), "honest_degraded_chat", "unknown"),
    ]
    for decision, branch, check in cases:
        receipt = reply_rejection_receipt(decision, rendered_via=branch, response_reason="r")
        assert receipt is not None and receipt["rejecting_check"] == check, (branch, receipt)


def test_a_published_reply_gets_no_receipt() -> None:
    for branch in ("model_final_wording", "reasoning_engine"):
        assert reply_rejection_receipt(_decision(output_text="fine"), rendered_via=branch, response_reason="r") is None
