"""Captions derived from public typed events — and nothing else.

A caption exists to tell the viewer what really happened in the product at
that moment. The only allowed source is the public typed runtime-event trace
(/api/runtime/events): the allowlist excludes the streaming content channel
entirely, so no model output chunk and nothing resembling hidden reasoning
can become a caption. Every caption text is redacted before it is returned.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ops.demo_studio.focus import event_epoch
from ops.demo_studio.journeys import PUBLIC_CAPTION_EVENTS
from ops.demo_studio.redact import RedactionEngine

#: Per-event caption phrasing. ``{message}`` receives the event's own public
#: message (redacted, truncated). Event types without a template fall back to
#: their type name plus message — still public data only.
CAPTION_TEMPLATES: dict[str, str] = {
    "task_received": "VOOL received the turn.",
    "task_classified": "The turn was classified and routed.",
    "task_completed": "The turn completed.",
    "task_interrupted": "The turn was interrupted.",
    "task_resumed": "The turn resumed.",
    "task_pending_approval": "Waiting for operator approval.",
    "tool_selected": "VOOL chose a tool for this part of the turn.",
    "tool_executed": "A tool really executed: {message}",
    "tool_failed": "A tool failed safely: {message}",
    "tool_loop_completed": "The tool loop closed.",
    "tool_synthesizing": "Synthesizing the answer from the tool results.",
    "web_retrieval_started": "Reaching out to live sources.",
    "web_retrieval_completed": "Live sources retrieved: {message}",
    "live_data_plan_created": "A live-data plan was created.",
    "live_data_plan_completed": "The live-data plan completed.",
    "model_lane_proof": "The answering lane was proven on the record: {message}",
    "attachment_staged": "An attachment was staged through the upload door: {message}",
    "attachment_bound": "The attachment was bound to the turn.",
    "attachment_released": "The attachment was released after the turn.",
    "attachment_refused": "An attachment was refused by the upload door: {message}",
    "permission_approved": "The operator approved the action.",
    "permission_denied": "The operator denied the dangerous action.",
    "task_envelope_rollback_completed": "The recorded turn was rolled back byte-exactly.",
    "attempt_claim_refused": "A lane claim was refused by the ownership gate.",
    "audit_budget_refused": "An action was refused by the audit budget.",
}

_MESSAGE_LIMIT = 90
_MIN_S = 1.2
_MAX_S = 4.0


def caption_text_for(event: Mapping[str, Any], engine: RedactionEngine) -> str | None:
    """The redacted caption sentence for one public event, or None."""
    event_type = str(event.get("event_type") or "")
    if event_type not in PUBLIC_CAPTION_EVENTS:
        return None
    template = CAPTION_TEMPLATES.get(event_type)
    if template is None:
        template = "{event_type}: {message}"
    message = engine.redact(str(event.get("message") or "")).strip()
    if "{message}" in template:
        if not message:
            text = template.split(": {message}")[0].split("{message}")[0].strip().rstrip(":")
            text = text or event_type
            return text + "."
        message = message if len(message) <= _MESSAGE_LIMIT else message[: _MESSAGE_LIMIT - 1] + "…"
        return template.format(message=message)
    return template


def build_captions(
    events: Sequence[Mapping[str, Any]],
    engine: RedactionEngine,
    *,
    video_t0_epoch: float | None,
) -> list[dict[str, Any]]:
    """Ordered caption rows {start_s, end_s, text, seq, event_type}."""
    allowlisted = [
        event
        for event in events
        if str(event.get("event_type") or "") in PUBLIC_CAPTION_EVENTS
    ]
    allowlisted.sort(key=lambda e: (event_epoch(e.get("created_at")), int(e.get("seq") or 0)))
    if video_t0_epoch is None:
        video_t0_epoch = event_epoch(allowlisted[0].get("created_at")) if allowlisted else 0.0
    rows: list[dict[str, Any]] = []
    for event in allowlisted:
        text = caption_text_for(event, engine)
        if not text:
            continue
        video_t = max(0.0, event_epoch(event.get("created_at")) - float(video_t0_epoch))
        start = max(0.0, video_t - 0.25)
        duration = min(_MAX_S, max(_MIN_S, 0.05 * len(text) + 1.0))
        if rows and start < rows[-1]["end_s"]:
            rows[-1]["end_s"] = round(max(rows[-1]["start_s"] + 0.4, start - 0.05), 3)
            start = max(start, rows[-1]["end_s"] + 0.05)
        rows.append(
            {
                "start_s": round(start, 3),
                "end_s": round(start + duration, 3),
                "text": text,
                "seq": int(event.get("seq") or 0),
                "event_type": str(event.get("event_type") or ""),
            }
        )
    return rows
