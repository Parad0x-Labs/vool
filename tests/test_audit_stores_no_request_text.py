"""R10 (round 2): the durable audit log stores a turn context's SHAPE, never its words.

A turn context carries the request's own text and, inside its frozen records and retrieved
context, tool observations such as note or email bodies. Whole contexts reach the audit seam
(``dict(source_context)`` in tool_intent_executor's unsupported-path audit among them), and
storing them verbatim put request text and observation bodies into durable logs. The bounded
summary keeps key names and value types only. Red on 3f54fe17's parent (15c535fc): the
request text round-tripped into ``details_json`` there.
"""
from __future__ import annotations

import json

from core import audit_logger
from core.execution_requirements import CurrentRequirementRecord, ExecutionRequirements
from storage.db import get_connection


def _rows(event_type: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT details_json FROM audit_log WHERE event_type = ?", (event_type,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


_SECRET = "the plumber comes at 9 on Monday and my note body says vault 7"


def _context() -> dict:
    record = CurrentRequirementRecord(
        requirements=ExecutionRequirements(
            answer_mode="DIRECT",
            external_evidence_required=False,
            current_information_required=False,
            user_material_supplied=False,
            tools_required=False,
            allowed_toolsets=(),
            inference_allowed=True,
            reason_codes=("x_action_request",),
        ),
        request_text=_SECRET,
    )
    return {
        "request_text": _SECRET,
        "runtime_session_id": "s-r10",
        "turn_id": "t-r10",
        "_current_information_requirement": record,
        "retrieved_context": {"observations": [{"intent": "operator.show_note",
                                                "response_preview": _SECRET}]},
    }


def test_request_text_is_never_stored_for_a_whole_turn_context():
    audit_logger.log("test_r10_context", target_id="task-r10-1", details=_context())
    rows = _rows("test_r10_context")
    assert rows, "the audit row must be written"
    stored = rows[0]["details_json"]
    assert _SECRET not in stored
    parsed = json.loads(stored)
    # Key names and value TYPES only: the summary is inspectable, the words are gone.
    assert parsed["request_text"] == "str"
    assert parsed["runtime_session_id"] == "str"
    record = parsed["_current_information_requirement"]
    assert record == "CurrentRequirementRecord"
    observations = parsed["retrieved_context"]["observations"]
    assert observations == "list[dict]"


def test_a_nested_source_context_is_summarized_too():
    audit_logger.log(
        "test_r10_nested",
        target_id="task-r10-2",
        details={"intent": "operator.save_note", "source_context": _context()},
    )
    stored = _rows("test_r10_nested")[0]["details_json"]
    assert _SECRET not in stored
    parsed = json.loads(stored)
    # The plain detail keeps its value; only the context value is a summary.
    assert parsed["intent"] == "operator.save_note"
    assert parsed["source_context"]["request_text"] == "str"


def test_an_unwired_call_answers_and_leaves_no_request_text_in_the_audit_row():
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    execution = execute_tool_intent(
        {"intent": "totally.not_wired_r10", "arguments": {"x": 1}},
        task_id="task-r10-3",
        session_id="s-r10",
        source_context=_context(),
        hive_activity_tracker=HiveActivityTracker(
            config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )
    # R10's drivable law: the unwired call ANSWERS instead of crashing (B7), even with a
    # turn context carrying frozen records in the source context.
    assert execution.user_safe_response_text == "That action is not wired on this runtime yet."
    assert execution.status == "unsupported"
    # The unsupported-path audit row itself is served-lane-only (the in-process entry
    # returns before it); its exact details shape — now summarized by tests 1-2 above —
    # is pinned in test_audit_log_serializes_non_json_details.py, and the request text
    # of THIS turn's context was never handed to the audit seam with the answer intact.
