"""NIA-014 first landing: every served turn self-reports its failure-stage verdict.

`finalize_turn_trace` now stamps `stage_verdict` (state + explain) into the
existing `turn.trace_completed` event, joined from events the turn already
emitted. These tests pin the classifier laws the mount relies on and the mount
itself (grep-level seam proof, same discipline as the paged-memory seam test).
"""
from __future__ import annotations

import pathlib

from core.turn_failure_stage import (
    TerminalState,
    classify_turn,
    explain,
    observation_from_trace,
)


def test_provider_error_is_classified_from_events():
    events = [
        {"event_type": "model.call_started", "message": ""},
        {"event_type": "model.call_failed", "message": "connection reset by peer"},
    ]
    assert classify_turn(observation_from_trace(events)) is TerminalState.PROVIDER_ERROR


def test_completed_call_clears_the_provider_error():
    events = [
        {"event_type": "model.call_started", "message": ""},
        {"event_type": "model.call_failed", "message": "timeout"},
        {"event_type": "model.call_completed", "message": ""},
    ]
    state = classify_turn(observation_from_trace(events))
    assert state is not TerminalState.PROVIDER_ERROR


def test_unknown_events_contribute_nothing_rather_than_a_guess():
    events = [{"event_type": "something.new", "message": "whatever"}]
    assert classify_turn(observation_from_trace(events)) is TerminalState.UNCLASSIFIED
    assert classify_turn(observation_from_trace(None)) is TerminalState.UNCLASSIFIED


def test_an_operator_stop_is_not_a_stage_failure_or_a_delivered_answer():
    """The runtime refuses the next lane attempt with `turn_cancelled` when the operator stops a
    turn mid-flight. That stop must classify as its own state — not success over text the stop
    prevented from being delivered, and not a provider error blaming a provider that answered."""
    events = [
        {"event_type": "model.call_started", "message": ""},
        {"event_type": "model.call_completed", "message": ""},
        {
            "event_type": "model_lane_failed",
            "message": "",
            "attempt_timings": [{"error": "turn_cancelled", "model_id": "m", "outcome": "failed"}],
        },
    ]
    observation = observation_from_trace(events)
    observation.raw_content = "text that was never delivered"
    observation.final_text = "text that was never delivered"
    assert classify_turn(observation) is TerminalState.OPERATOR_STOPPED


def test_the_operator_stop_is_read_from_the_nested_details_shape_too():
    events = [
        {
            "event_type": "model_routing_failed",
            "details": {"error": "turn_cancelled"},
        }
    ]
    assert classify_turn(observation_from_trace(events)) is TerminalState.OPERATOR_STOPPED


def test_a_lane_failure_without_cancellation_is_not_an_operator_stop():
    events = [
        {"event_type": "model.call_started", "message": ""},
        {"event_type": "model_lane_failed", "message": "", "error": "500 Server Error"},
    ]
    assert classify_turn(observation_from_trace(events)) is not TerminalState.OPERATOR_STOPPED


def test_every_state_has_an_explanation():
    for state in TerminalState:
        assert explain(state).strip(), f"{state} ships without an explanation"


def test_the_mount_is_real_in_finalize_turn_trace():
    """The verdict must be stamped into the turn.trace_completed details — the
    served surface, not just the module."""
    source = pathlib.Path("core/web/api/runtime.py").read_text()
    finalize = source[source.index("def finalize_turn_trace"):]
    emit_block = finalize[finalize.index('"turn.trace_completed"'):]
    assert "classify_turn" in finalize, "finalize must compute the verdict"
    assert '"stage_verdict": stage_verdict' in emit_block, (
        "the verdict must ride the turn.trace_completed details"
    )
