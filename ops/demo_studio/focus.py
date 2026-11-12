"""Event-justified focus: the camera moves only when a real event says so.

Every zoom segment cites the public typed event that justifies it and carries
the DOM geometry captured from the live page at that moment. The evidence
manifest publishes every justification, so a viewer can audit whether each
camera move was earned. A zoom without both an event and real geometry is a
fabrication and is refused before it can reach a render.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def event_epoch(value: Any) -> float:
    """Event time as epoch seconds; the served trace emits ISO strings."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


@dataclass(frozen=True)
class FocusRule:
    """One mapping from a public event type to an on-screen region."""

    event_type: str
    selector: str
    region: str


@dataclass(frozen=True)
class FocusEntry:
    """One zoom segment, justified by exactly one real event."""

    start_s: float
    end_s: float
    region: str
    selector: str
    box: dict[str, int] = field(default_factory=dict)
    justified_by: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "start_s": round(self.start_s, 3),
            "end_s": round(self.end_s, 3),
            "region": self.region,
            "selector": self.selector,
            "box": dict(self.box),
            "justified_by": dict(self.justified_by),
        }


#: The focus vocabulary. Events that matter visually map to the real page
#: selectors they manifest in; events without a rule never zoom.
FOCUS_RULES: dict[str, FocusRule] = {
    "tool_selected": FocusRule("tool_selected", ".task-card", "activity-card"),
    "tool_executed": FocusRule("tool_executed", ".task-card", "activity-card"),
    "tool_failed": FocusRule("tool_failed", ".task-card", "activity-card"),
    "web_retrieval_started": FocusRule("web_retrieval_started", ".task-card", "activity-card"),
    "web_retrieval_completed": FocusRule("web_retrieval_completed", ".task-card", "activity-card"),
    "attachment_staged": FocusRule("attachment_staged", "#attachStrip", "attach-strip"),
    "attachment_refused": FocusRule("attachment_refused", "#attachStrip", "attach-strip"),
    "task_pending_approval": FocusRule("task_pending_approval", "#permBar", "permission-bar"),
    "permission_approved": FocusRule("permission_approved", "#permBar", "permission-bar"),
    "permission_denied": FocusRule("permission_denied", "#permBar", "permission-bar"),
    "model_lane_proof": FocusRule("model_lane_proof", "#modelCtrl", "model-control"),
    "task_envelope_rollback_completed": FocusRule(
        "task_envelope_rollback_completed", "#log", "transcript"
    ),
}

#: Zoom window around the justifying event's video time.
PRE_S = 0.4
POST_S = 2.5


def focus_region_for_event(event_type: str) -> FocusRule | None:
    return FOCUS_RULES.get(event_type)


def plan_focus_from_events(
    events: Sequence[Mapping[str, Any]],
    boxes: Mapping[int, dict[str, int] | None],
    *,
    video_t0_epoch: float,
    pre_s: float = PRE_S,
    post_s: float = POST_S,
) -> list[FocusEntry]:
    """Build the zoom plan from the real event trace + captured geometry.

    An entry is produced only when BOTH hold: the event type has a focus rule,
    and the driver captured a live DOM box for it at event time. Anything else
    stays a wide shot.
    """
    entries: list[FocusEntry] = []
    for event in sorted(events, key=lambda e: (event_epoch(e.get("created_at")), int(e.get("seq") or 0))):
        event_type = str(event.get("event_type") or "")
        rule = FOCUS_RULES.get(event_type)
        if rule is None:
            continue
        seq = int(event.get("seq") or 0)
        box = boxes.get(seq)
        if not box:
            continue
        video_t = event_epoch(event.get("created_at")) - float(video_t0_epoch)
        start = max(0.0, video_t - pre_s)
        entries.append(
            FocusEntry(
                start_s=start,
                end_s=start + post_s,
                region=rule.region,
                selector=rule.selector,
                box={k: int(v) for k, v in box.items()},
                justified_by={"seq": seq, "event_type": event_type},
            )
        )
    entries.sort(key=lambda e: e.start_s)
    return entries


def validate_focus_plan(plan: Sequence[FocusEntry], *, event_seqs: set[int]) -> None:
    """Refuse any segment that is not backed by a real, existing event."""
    for entry in plan:
        justification = entry.justified_by or {}
        if "seq" not in justification or "event_type" not in justification:
            raise ValueError(
                f"zoom on {entry.region} is unjustified: no citing event; "
                "every zoom must be backed by a real typed event"
            )
        seq = int(justification.get("seq") or 0)
        if seq not in event_seqs:
            raise ValueError(
                f"zoom on {entry.region} cites event seq {seq} which does not "
                "exist in the recorded trace — a cinematic cut with no event "
                "behind it is a fabrication"
            )
        if not entry.box:
            raise ValueError(
                f"zoom on {entry.region} (seq {seq}) has no captured DOM geometry"
            )
