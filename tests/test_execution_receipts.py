from __future__ import annotations

from core.execution import receipts as extracted_receipts
from core.execution.models import ToolIntentExecution
from core.tool_intent_executor import (
    _execution_from_receipt,
    _execution_to_receipt,
    _inject_idempotency_key,
    _normalize_payload,
)


def test_receipt_facade_matches_extracted_receipt_helpers() -> None:
    execution = ToolIntentExecution(
        handled=True,
        ok=True,
        status="executed",
        response_text="done",
        user_safe_response_text="done",
        mode="tool_executed",
        tool_name="hive.create_topic",
        details={"topic_id": "topic-123"},
        learned_plan=None,
    )
    receipt = {
        "execution": extracted_receipts.execution_to_receipt(execution),
        "idempotency_key": "receipt-123",
    }

    assert _normalize_payload('{"intent":"hive.create_topic","arguments":{"title":"Topic"}}') == extracted_receipts.normalize_payload(
        '{"intent":"hive.create_topic","arguments":{"title":"Topic"}}'
    )
    assert _inject_idempotency_key("hive.create_topic", {"title": "Topic"}, idempotency_key="receipt-123") == extracted_receipts.inject_idempotency_key(
        "hive.create_topic",
        {"title": "Topic"},
        idempotency_key="receipt-123",
    )
    assert _execution_to_receipt(execution) == extracted_receipts.execution_to_receipt(execution)
    assert _execution_from_receipt(receipt) == extracted_receipts.execution_from_receipt(receipt)
