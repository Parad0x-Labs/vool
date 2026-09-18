"""Finding D, 2026-08-04: the visible step counter must reflect the model-driven audit ledger too.

`core/agent_runtime/stepped_audit.py::_emit_call_ledger` emits `audit_step` (one per model call:
nominate/challenge/prove/synthesize) and `audit_budget_refused` (when the call budget stops the
search), joined from `AuditCallLedger.as_rows()`/`.refusals`. Before this fix `_DIRECT_MAP`
(`core/task_event_model.py`) had no entry for either event type, so `build_task_event` silently
dropped them and the "N actions" chip froze at whatever the DETERMINISTIC evidence pass (list_tree,
manifest reads, ...) had counted before the model-driven verification call even started -- measured
live: a real audit hit a genuine 180s model timeout on its nomination call and the counter never
moved to reflect it.
"""
from __future__ import annotations

from core.agent_runtime.audit_call_budget import AuditCall
from core.task_event_model import build_task_event


def _audit_step_event(call: AuditCall) -> dict:
    row = call.as_dict()
    return {
        "event_type": "audit_step",
        "message": f"audit call {row['call']} — {row['purpose']} on {row['model_name']}: {row['result']}",
        **row,
    }


def test_a_successful_audit_call_becomes_a_completed_tool_event() -> None:
    call = AuditCall(number=1, step="nominate", context_source="full_file_excerpt", result="answered")
    event = build_task_event(_audit_step_event(call))

    assert event is not None
    assert event["type"] == "tool.completed"
    assert event["status"] == "completed"


def test_a_failed_audit_call_becomes_a_failed_tool_event_not_a_silent_success() -> None:
    """The ledger row's own `result` field ('error:...'/'rejected:...') is the ground truth for
    which outcome this was -- a timeout must not be reported as a routine completed step."""

    call = AuditCall(
        number=2, step="nominate", context_source="full_file_excerpt",
        result="error: read timeout after 180s",
    )
    event = build_task_event(_audit_step_event(call))

    assert event is not None
    assert event["type"] == "tool.failed"
    assert event["status"] == "failed"


def test_a_rejected_audit_call_also_becomes_a_failed_event() -> None:
    call = AuditCall(
        number=3, step="prove", context_source="proof_result", result="rejected: no provider available",
    )
    event = build_task_event(_audit_step_event(call))

    assert event is not None
    assert event["type"] == "tool.failed"


def test_a_budget_refusal_reaches_the_counter_as_a_failed_step() -> None:
    event = build_task_event(
        {
            "event_type": "audit_budget_refused",
            "message": "audit stopped: the audit's bounded call budget (8 model calls) was spent",
            "reason": "the audit's bounded call budget (8 model calls) was spent",
            "calls": 8,
        }
    )

    assert event is not None
    assert event["type"] == "tool.failed"
    assert event["status"] == "failed"


def test_events_carry_a_tool_name_even_with_no_explicit_one() -> None:
    """`tool_name` is never set on these rows -- `_DIRECT_MAP`'s wiring must still produce a
    non-empty, purpose-derived tool label rather than leaving `tool` unset (which drops the
    stage-derivation step downstream)."""

    call = AuditCall(number=1, step="challenge", context_source="finding_and_claim_only", result="answered")
    event = build_task_event(_audit_step_event(call))

    assert event is not None
    assert event["tool"] == "audit.challenge"


def test_a_real_ledger_produces_one_countable_event_per_call() -> None:
    """End-to-end through the real ledger object, not a hand-built payload."""

    from core.agent_runtime.audit_call_budget import AuditCallLedger

    ledger = AuditCallLedger()
    for _ in range(4):  # the `nominate` step's own ceiling (DEFAULT_STEP_CEILINGS)
        ledger.open_call(step="nominate", context_source="full_file_excerpt").result = "answered"
    ledger.open_call(step="challenge", context_source="finding_and_claim_only").result = "answered"
    ledger.may_call("nominate")  # ceiling already spent -> records a refusal

    events = [build_task_event(_audit_step_event(call)) for call in ledger.calls]
    assert all(event is not None for event in events)
    assert [event["type"] for event in events] == ["tool.completed"] * len(ledger.calls)
    assert len(ledger.calls) == 5

    refusal_events = [
        build_task_event(
            {
                "event_type": "audit_budget_refused",
                "message": f"audit stopped: {reason}",
                "reason": reason,
                "calls": ledger.count,
            }
        )
        for reason in ledger.refusals
    ]
    assert refusal_events, "the ceiling must have actually refused a call for this test to mean anything"
    assert all(event["type"] == "tool.failed" for event in refusal_events)
