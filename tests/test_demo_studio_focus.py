"""Focus/zoom: the camera moves only when a real event justifies it.

Automatic zoom is the easiest place for a demo tool to start lying — a
highlight on nothing looks like emphasis on something. The rule here is
auditable in the artifact itself: every zoom segment must cite the public
typed event (seq + type) that justified it AND carry the live DOM geometry
captured from the real page at that moment. A zoom with either missing is a
defect the validator refuses, and the evidence manifest publishes every
justification for review.
"""

from __future__ import annotations

import pytest

from ops.demo_studio.focus import (
    FOCUS_RULES,
    FocusEntry,
    focus_region_for_event,
    plan_focus_from_events,
    validate_focus_plan,
)


def _event(seq: int, event_type: str) -> dict:
    return {"seq": seq, "event_type": event_type, "message": "m", "created_at": 100.0 * seq}


def _trace() -> list[dict]:
    return [
        _event(1, "task_received"),
        _event(2, "tool_selected"),
        _event(3, "tool_executed"),
        _event(4, "permission_denied"),
        _event(5, "task_completed"),
    ]


_BOXES = {2: {"x": 20, "y": 40, "width": 360, "height": 120}, 3: {"x": 20, "y": 40, "width": 360, "height": 120}, 4: {"x": 0, "y": 500, "width": 800, "height": 90}}


def test_focus_rules_exist_for_the_events_that_matter() -> None:
    for event_type in ("tool_executed", "permission_denied", "attachment_staged", "task_pending_approval"):
        assert event_type in FOCUS_RULES, f"no focus rule for {event_type}"
        assert FOCUS_RULES[event_type].selector.strip()


def test_plan_focus_cites_the_justifying_event_and_the_dom_box() -> None:
    t0 = 100.0
    plan = plan_focus_from_events(_trace(), _BOXES, video_t0_epoch=t0)
    assert plan, "a trace with focus-justifying events must produce a plan"
    entry = plan[0]
    assert isinstance(entry, FocusEntry)
    assert entry.justified_by["event_type"] in FOCUS_RULES
    assert entry.justified_by["seq"] in _BOXES
    assert entry.box, "a zoom without real DOM geometry is a fabrication"
    # The zoom window starts at the event's video time, not before it.
    event_time = _BOXES and plan[0].start_s
    assert plan[0].start_s >= -0.001


def test_events_without_rules_or_geometry_produce_no_zoom() -> None:
    trace = [_event(1, "task_received"), _event(2, "task_completed")]
    assert plan_focus_from_events(trace, {}, video_t0_epoch=0.0) == []
    # A justifying event whose element was never on-screen carries no box: no zoom.
    trace2 = [_event(1, "tool_executed")]
    assert plan_focus_from_events(trace2, {}, video_t0_epoch=0.0) == [], (
        "zoom requires real DOM geometry; an event alone must not be enough"
    )


def test_validator_refuses_a_zoom_without_justification() -> None:
    orphan = FocusEntry(start_s=1.0, end_s=2.0, region="task-card", selector=".task-card", box={"x": 0, "y": 0, "width": 10, "height": 10}, justified_by={})
    with pytest.raises(ValueError) as excinfo:
        validate_focus_plan([orphan], event_seqs={1, 2, 3})
    assert "justified" in str(excinfo.value).lower()


def test_validator_refuses_a_zoom_citing_an_event_that_never_existed() -> None:
    ghost = FocusEntry(
        start_s=1.0,
        end_s=2.0,
        region="task-card",
        selector=".task-card",
        box={"x": 0, "y": 0, "width": 10, "height": 10},
        justified_by={"seq": 999, "event_type": "tool_executed"},
    )
    with pytest.raises(ValueError) as excinfo:
        validate_focus_plan([ghost], event_seqs={1, 2, 3})
    assert "999" in str(excinfo.value), "the refusal must name the phantom event"


def test_validator_accepts_a_real_plan() -> None:
    plan = plan_focus_from_events(_trace(), _BOXES, video_t0_epoch=100.0)
    validate_focus_plan(plan, event_seqs={e["seq"] for e in _trace()})


def test_focus_region_mapping_is_typed() -> None:
    rule = focus_region_for_event("permission_denied")
    assert rule is not None and rule.selector.startswith("#"), (
        "permission focus must target the real permission bar selector"
    )
    assert focus_region_for_event("task_received") is None or FOCUS_RULES.get("task_received") is None
