"""Server-side turn candidates for the chat UI's bug-report flow.

The browser must never handle raw diagnostics: this module reads the runtime's own
event records for a session, derives which turns FAILED, and returns already-sanitized
material (redacted messages, identifier-validated lanes/tools/models, shape-safe log
lines). Conversation content is deliberately NOT consulted -- message bodies never enter
a bug report through this path; the user types expected/actual themselves.
"""
from __future__ import annotations

import json

from core.bug_report.allowlist import sanitize_identifier
from core.bug_report.redaction import redact_text

MAX_TURNS = 10
MAX_EVENTS_SCANNED = 200
MAX_LINES_PER_TURN = 200

# Detail keys that may carry an error/stack payload worth offering as error_text.
_ERROR_DETAIL_KEYS = ("error", "stack", "traceback", "failure_detail")
_COMPONENT_DETAIL_KEYS = ("lane", "tool", "model", "lane_id", "tool_id", "model_id")


def _sanitize(value: str) -> str:
    sanitized, _summary = redact_text(str(value or ""))
    return sanitized


def _is_failure_type(event_type: str) -> bool:
    return "fail" in str(event_type or "").lower()


def session_candidates(session_id: str, *, limit_turns: int = MAX_TURNS) -> list[dict]:
    """Failed-turn candidates for a session, newest first, fully sanitized."""
    from core.runtime_continuity import list_runtime_session_events

    clean_session = str(session_id or "").strip()
    if not clean_session:
        return []
    events = list_runtime_session_events(clean_session, limit=MAX_EVENTS_SCANNED)

    turns: dict[str, list[dict]] = {}
    order: list[str] = []
    for event in events:
        turn_key = str(event.get("turn_key") or "").strip()
        if not turn_key:
            continue
        if turn_key not in turns:
            turns[turn_key] = []
            order.append(turn_key)
        turns[turn_key].append(event)

    candidates: list[dict] = []
    for turn_key in reversed(order):  # newest first (events are oldest-first)
        group = turns[turn_key]
        failure_events = [e for e in group if _is_failure_type(str(e.get("event_type") or ""))]
        if not failure_events:
            continue
        failure_types: list[str] = []
        for event in failure_events:
            event_type = str(event.get("event_type") or "")
            if event_type and event_type not in failure_types:
                failure_types.append(event_type)

        last_failure = failure_events[-1]
        summary = _sanitize(last_failure.get("message") or "")

        error_text = ""
        for event in reversed(group):
            details = _event_details(event)
            for key in _ERROR_DETAIL_KEYS:
                value = details.get(key)
                if isinstance(value, str) and value.strip():
                    error_text = _sanitize(value)
                    break
            if error_text:
                break

        lanes: list[str] = []
        tools: list[str] = []
        models: list[str] = []
        for event in group:
            details = _event_details(event)
            for key in _COMPONENT_DETAIL_KEYS:
                value = details.get(key)
                if not isinstance(value, str) or not value:
                    continue
                bucket = models if "model" in key else tools if "tool" in key else lanes
                identifier = sanitize_identifier(value, field=key)
                if identifier and identifier not in bucket:
                    bucket.append(identifier)

        log_lines = [
            _log_line(event) for event in group[-MAX_LINES_PER_TURN:]
        ]

        candidates.append(
            {
                "turn_key": turn_key,
                "client_turn_id": str(group[-1].get("client_turn_id") or ""),
                "ts": str(group[-1].get("created_at") or ""),
                "failed": True,
                "failure_types": failure_types,
                "summary": summary,
                "error_text": error_text,
                "log_lines": log_lines,
                "lanes": lanes,
                "tools": tools,
                "models": models,
            }
        )
        if len(candidates) >= max(1, min(int(limit_turns), MAX_TURNS)):
            break
    return candidates


_RESERVED_EVENT_KEYS = {
    "seq", "session_id", "event_type", "message", "created_at", "client_turn_id", "turn_key",
}


def _event_details(event: dict) -> dict:
    """list_runtime_session_events merges the persisted details_json INTO the event dict
    (top-level keys); treat everything outside the reserved columns as detail fields."""
    merged = {}
    raw = event.get("details_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                merged.update(parsed)
        except (ValueError, TypeError):
            pass
    for key, value in event.items():
        if key not in _RESERVED_EVENT_KEYS and not isinstance(value, (dict, list)):
            merged.setdefault(key, value)
    return merged


def _log_line(event: dict) -> str:
    event_type = str(event.get("event_type") or "status")
    level = "ERROR" if _is_failure_type(event_type) else "INFO"
    created_at = str(event.get("created_at") or "")
    message = _sanitize(event.get("message") or "")
    return f"{created_at} {level} {event_type} {message}".strip()


__all__ = ["session_candidates"]
