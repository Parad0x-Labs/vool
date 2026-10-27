"""The audit seam must record a turn context it cannot fully serialize, not crash the turn.

Red on b744af81: an unwired model tool call logs its unsupported-path audit with
``details={"source_context": dict(source_context)}`` (core/tool_intent_executor.py:1758), a turn
context carries the frozen ``CurrentRequirementRecord`` dataclass (core/execution_requirements.py),
and ``audit_logger.log`` serialized with a bare ``json.dumps`` — TypeError, HTTP 500, and the
turn's own "That action is not wired on this runtime yet." answer never returned.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from enum import Enum

from core import audit_logger
from core.execution_requirements import CurrentRequirementRecord, ExecutionRequirements
from storage.db import get_connection


class _Flavor(Enum):
    PLAIN = "plain"


@dataclass(frozen=True)
class _Nested:
    note: str
    when: dt.datetime


def _rows(event_type: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT details_json FROM audit_log WHERE event_type = ?", (event_type,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def test_log_records_a_turn_context_carrying_the_frozen_requirement_record():
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
        request_text="am I free on Thursday at 3pm?",
    )
    details = {"intent": "operator.schedule_reminder", "arguments": {"text": "x"},
               "source_context": {"_current_information_requirement": record,
                                  "session_id": "s-1"}}
    audit_logger.log("tool_intent_unsupported", target_id="task-1", details=details)
    rows = _rows("tool_intent_unsupported")
    assert rows, "the audit row must be written"
    stored = json.loads(rows[0]["details_json"])
    assert stored["intent"] == "operator.schedule_reminder"
    # The frozen record keeps its structure: a plain dict of its fields, still inspectable.
    record_back = stored["source_context"]["_current_information_requirement"]
    assert record_back["request_text"] == "am I free on Thursday at 3pm?"
    assert record_back["requirements"]["reason_codes"] == ["x_action_request"]


def test_log_renders_datetimes_enums_sets_and_unknown_objects_without_raising():
    class Opaque:
        def __repr__(self) -> str:  # pragma: no cover - trivial
            return "<opaque>"

    audit_logger.log(
        "test_non_json_details",
        target_id="task-2",
        details={"when": dt.datetime(2026, 10, 27, 5, 30, tzinfo=dt.timezone.utc),
                 "flavor": _Flavor.PLAIN,
                 "tags": {"a", "b"},
                 "nested": _Nested(note="n", when=dt.date(2026, 10, 27)),
                 "opaque": Opaque(),
                 "deep": {"list": [dt.timedelta(days=1)]}},
    )
    rows = _rows("test_non_json_details")
    assert rows
    stored = json.loads(rows[0]["details_json"])
    assert stored["when"] == "2026-10-27T05:30:00+00:00"
    assert stored["flavor"] == "plain"
    assert sorted(stored["tags"]) == ["a", "b"]
    assert stored["nested"] == {"note": "n", "when": "2026-10-27"}
    assert isinstance(stored["opaque"], str)
    assert isinstance(stored["deep"]["list"][0], str)


def test_the_unsupported_path_audit_shape_is_json_safe():
    """The exact dict shape tool_intent_executor's unsupported path writes."""
    from core.execution_requirements import requirements_for

    source_context: dict = {"session_id": "s-2", "runtime_session_id": "s-2"}
    requirements_for("show my agenda for today", task_class="unknown", source_context=source_context)
    audit_logger.log(
        "tool_intent_unsupported",
        target_id="task-3",
        details={"intent": "operator.apple_note_list", "arguments": {},
                 "source_context": dict(source_context)},
    )
    assert _rows("tool_intent_unsupported")[-1]["details_json"]
