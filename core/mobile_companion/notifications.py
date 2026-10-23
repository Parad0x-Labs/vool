"""Per-device delivery projection of the canonical desktop inbox.

Inbox items stay owned by notification_center. This module owns only a phone's
noise choices, delivery/read cursors and bounded quiet-hour backlog.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone

TYPES = {
    "task_completed": "A task finished on your computer.",
    "task_failed": "A task failed on your computer.",
    "task_pending_approval": "VOOL needs your decision.",
    "task_cancelled": "A task stopped on your computer.",
    "payment_pending": "A payment needs your decision.",
    "payment_done": "A payment finished.",
    "payment_refused": "A payment was refused or failed.",
    "desktop_inbox": "A new item is in your computer's inbox.",
    "calendar_alert": "A calendar event needs a look.",
    "calendar_catch_up": "You have missed calendar alerts.",
    "reminder": "You have a reminder.",
    "model_offer": "A model offer is available.",
    "model_market": "A model update is available.",
    "market_watch": "A market watch needs a look.",
}
IMPORTANT = frozenset({"task_failed", "task_pending_approval", "payment_pending", "payment_done", "payment_refused"})
PAYMENT_TERMINALS = frozenset({"payment_done", "payment_refused"})
PRESETS = frozenset({"everything", "important", "off"})
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def defaults(preset="important"):
    return {
        "preset": preset,
        "types": {
            kind: {"enabled": preset == "everything" or (preset == "important" and kind in IMPORTANT), "details": False}
            for kind in TYPES
        },
        "quiet": {
            "enabled": False,
            "start": "22:00",
            "end": "07:00",
            "offset_minutes": 0,
            "approvals_through": True,
            "payments_through": True,
        },
    }


def validate(change, previous):
    if not isinstance(change, dict) or set(change) - {"preset", "types", "quiet"}:
        raise ValueError("Invalid notification settings.")
    result = copy.deepcopy(previous)
    if "preset" in change:
        if change["preset"] not in PRESETS:
            raise ValueError("Choose a notification preset.")
        result = defaults(change["preset"])
        result["quiet"] = copy.deepcopy(previous["quiet"])
    if "types" in change:
        values = change["types"]
        if not isinstance(values, dict) or set(values) - TYPES.keys():
            raise ValueError("Unknown notification type.")
        for kind, options in values.items():
            if (
                not isinstance(options, dict)
                or set(options) - {"enabled", "details"}
                or any(type(v) is not bool for v in options.values())
            ):
                raise ValueError("Notification switches must be on or off.")
            result["types"][kind].update(options)
        result["preset"] = "custom"
    if "quiet" in change:
        quiet = change["quiet"]
        if not isinstance(quiet, dict) or set(quiet) - result["quiet"].keys():
            raise ValueError("Invalid quiet hours.")
        for name, value in quiet.items():
            if name in {"enabled", "approvals_through", "payments_through"} and type(value) is not bool:
                raise ValueError("Quiet-hour switches must be on or off.")
            if name in {"start", "end"} and (not isinstance(value, str) or not _CLOCK.fullmatch(value)):
                raise ValueError("Use a time such as 22:00.")
            if name == "offset_minutes" and (type(value) is not int or not -840 <= value <= 840):
                raise ValueError("Invalid local time offset.")
        result["quiet"].update(quiet)
    return result


def _now():
    return datetime.now(timezone.utc)


def _head():
    from storage.db import get_connection

    connection = get_connection()
    try:
        return int(connection.execute("SELECT COALESCE(MAX(seq), 0) FROM notification_items").fetchone()[0])
    finally:
        connection.close()


def initial_state():
    head = _head()
    return {
        "preferences": defaults(),
        "source_after": head,
        "archive_after": head,
        "events": [],
        "held": [],
        "next_seq": 1,
        "delivery_ack": 0,
        "delivery_issued": 0,
        "read_after": 0,
    }


def _mandatory(device):
    from core.mobile_companion.policy import enabled

    return (
        PAYMENT_TERMINALS if enabled(device, "payment_propose") or enabled(device, "payment_approve") else frozenset()
    )


def _outer(device):
    from core.mobile_companion.policy import enabled

    return enabled(device, "alerts")


def _eligible(event, device):
    audience = event.get("audience_device_ids")
    return audience is None or (isinstance(audience, list) and device["device_id"] in audience)


def _forced(event, device):
    return (
        event.get("security_notice") in {"device_paired", "device_revoked"}
        and isinstance(event.get("audience_device_ids"), list)
        and _eligible(event, device)
    )


def _record_allowed(event, device):
    from core.mobile_companion.policy import enabled

    if not _eligible(event, device):
        return False
    if _forced(event, device) or _outer(device):
        return True
    if event["event_type"].startswith("payment_"):
        return enabled(device, "wallet_view") or bool(_mandatory(device))
    return event["event_type"] == "task_pending_approval" and enabled(device, "history")


def _new_for_device(event, device):
    if not event.get("occurred_at"):
        return True
    try:
        return datetime.fromisoformat(event["occurred_at"]) >= datetime.fromisoformat(device["paired_at"])
    except (ValueError, KeyError, TypeError):
        return False


def _enabled(preferences, kind, device, event=None):
    if event and _forced(event, device):
        return True
    return kind in _mandatory(device) or preferences["types"][kind]["enabled"]


def _quiet(preferences, kind, device, *, presentation=False, event=None):
    if event and _forced(event, device) and not presentation:
        return False
    if kind in _mandatory(device) and not presentation:
        return False
    value = preferences["quiet"]
    if not value["enabled"]:
        return False
    if kind == "task_pending_approval" and value["approvals_through"]:
        return False
    if kind.startswith("payment_") and value["payments_through"]:
        return False
    local = _now() + timedelta(minutes=value["offset_minutes"])
    now = local.hour * 60 + local.minute
    start, end = [int(text[:2]) * 60 + int(text[3:]) for text in [value["start"], value["end"]]]
    return start <= now < end if start < end else (now >= start or now < end) if start != end else False


def _source(after, limit):
    from core.operator import notification_center as center
    from storage.db import get_connection

    connection = get_connection()
    try:
        # An ascending projection, not the UI's newest-first window: a burst
        # larger than one page must not skip the first items.
        ids = connection.execute(
            "SELECT seq, notification_id FROM notification_items WHERE seq > ? ORDER BY seq ASC LIMIT ?", (after, limit)
        ).fetchall()
    finally:
        connection.close()
    for row in ids:
        item = center.load_item(row["notification_id"])
        yield int(row["seq"]), item


def _project(item, *, preserve_records=False):
    if not item or item.get("source_kind") == "test":
        return None
    payload = item.get("payload") or {}
    event_type = str(payload.get("event_type") or "")
    security_record = payload.get("security_notice") in {"device_paired", "device_revoked"}
    durable_record = event_type.startswith("payment_") or event_type == "task_pending_approval" or security_record
    if (item.get("dismissed_at") or item.get("superseded_at")) and not (
        (preserve_records and durable_record) or security_record
    ):
        return None
    source = str(item.get("source_kind") or "")
    kind = event_type if event_type in TYPES else source if source in TYPES else "desktop_inbox"
    screen = (
        "approvals"
        if kind == "task_pending_approval"
        else "wallet"
        if kind.startswith("payment_")
        else "chats"
        if kind.startswith("task_")
        else "notifications"
    )
    return {
        "source_id": item["notification_id"],
        "source_seq": int(item["seq"]),
        "event_type": kind,
        "security_notice": payload.get("security_notice"),
        "audience_device_ids": payload.get("audience_device_ids"),
        "created_at": item["created_at"],
        "session_id": str(item.get("session_id") or ""),
        "occurred_at": str(payload.get("occurred_at") or ""),
        "target": {
            "screen": screen,
            "id": str(payload.get("proposal_id") or item.get("session_id") or item["notification_id"]),
        },
        "message": TYPES[kind],
        "detail": str(item.get("title") or ""),
        "mobile_kind": "failure"
        if kind in {"task_failed", "payment_refused"}
        else "completion"
        if kind in {"task_completed", "payment_done"}
        else "info",
    }


def _public(event, preferences, device):
    value = dict(event)
    value.pop("audience_device_ids", None)
    kind = event["event_type"]
    from core.mobile_companion.policy import enabled

    details_allowed = enabled(device, "wallet_view" if kind.startswith("payment_") else "history")
    value["os_notification_allowed"] = bool(
        _outer(device)
        and preferences["types"][kind]["enabled"]
        and _new_for_device(event, device)
        and not _quiet(preferences, kind, device, presentation=True)
    )
    value["lock_screen_details"] = bool(preferences["types"][kind]["details"] and details_allowed)
    if not value["lock_screen_details"]:
        value.pop("detail", None)
    return value


def _archive_new(state, device):
    from core.mobile_companion.notification_archive import append

    records = []
    for sequence, item in _source(state.get("archive_after", state["source_after"]), 200):
        state["archive_after"] = sequence
        event = _project(item, preserve_records=True)
        if (
            event
            and _record_allowed(event, device)
            and (
                event["event_type"].startswith("payment_")
                or event["event_type"] == "task_pending_approval"
                or _forced(event, device)
            )
        ):
            records.append(event)
    append(device["device_id"], records)


def preferences_get(_params, *, service, device, **_kw):
    def read(state, current):
        return {
            "preferences": state["preferences"],
            "types": list(TYPES),
            "mandatory": sorted(_mandatory(current)),
            "alerts_allowed": _outer(current),
        }

    return 200, service.store.edit_notifications(str(device["device_id"]), read)


def preferences_set(params, *, service, device, **_kw):
    def update(state, current):
        state["preferences"] = validate(params, state["preferences"])
        for key in ["events", "held"]:
            state[key] = [
                event for event in state[key] if _enabled(state["preferences"], event["event_type"], current, event)
            ]
        return {"preferences": state["preferences"], "mandatory": sorted(_mandatory(current))}

    try:
        result = service.store.edit_notifications(str(device["device_id"]), update)
    except ValueError as error:
        return 400, {"error": str(error), "code": "invalid_notification_preferences"}
    service.store.append_receipt("notifications.preferences_changed", device_id=str(device["device_id"]))
    return 200, result


def poll(params, *, service, device, **_kw):
    from core.mobile_companion.payments import sync_wallet_events

    sync_wallet_events(device, service)

    def update(state, current):
        after = params.get("after", 0)
        if type(after) is not int or not 0 <= after <= state["delivery_issued"]:
            raise ValueError("Invalid delivery cursor.")
        limit = params.get("limit", 60)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Invalid notification limit.")
        state["delivery_ack"] = max(state["delivery_ack"], after)
        preferences = state["preferences"]
        outer = _outer(current)
        mandatory = _mandatory(current)
        _archive_new(state, current)
        if not outer and not mandatory:
            service.store.append_receipt("notifications.desktop_gate_refused", device_id=str(current["device_id"]))
        if not outer:
            state["events"] = [
                event for event in state["events"] if (event["event_type"] in mandatory or _forced(event, current))
            ]
            state["held"] = [
                event for event in state["held"] if (event["event_type"] in mandatory or _forced(event, current))
            ]
        candidates = state["held"]
        state["held"] = []
        released_records = []
        room = max(0, min(200, 1000 - len(state["events"]) - len(candidates)))
        for sequence, item in _source(state["source_after"], room) if room else []:
            state["source_after"] = sequence
            event = _project(item)
            if (
                event
                and _eligible(event, current)
                and _new_for_device(event, current)
                and (outer or event["event_type"] in mandatory or _forced(event, current))
                and _enabled(preferences, event["event_type"], current, event)
            ):
                candidates.append(event)
        for event in sorted(candidates, key=lambda value: value["source_seq"]):
            if not _enabled(preferences, event["event_type"], current, event):
                continue
            if _quiet(preferences, event["event_type"], current, event=event):
                state["held"].append(event)
            else:
                event["seq"] = state["next_seq"]
                state["next_seq"] += 1
                state["events"].append(event)
                released_records.append(event)
        from core.mobile_companion.notification_archive import append

        append(current["device_id"], released_records)
        selected = [event for event in state["events"] if event["seq"] > state["delivery_ack"]][:limit]
        next_after = max([state["delivery_ack"]] + [event["seq"] for event in selected])
        state["delivery_issued"] = max(state["delivery_issued"], next_after)
        return {
            "events": [_public(event, preferences, current) for event in selected],
            "next_after": next_after,
            "badge": sum(event["seq"] > state["read_after"] for event in state["events"]),
            "attention": any(
                event["seq"] > state["read_after"]
                and event["event_type"] in {"task_pending_approval", "payment_pending"}
                for event in state["events"]
            ),
            "gate": bool(outer or mandatory),
        }

    try:
        return 200, service.store.edit_notifications(str(device["device_id"]), update)
    except ValueError as error:
        return 400, {"error": str(error), "code": "invalid_notification_cursor"}


def feed(params, *, service, device, **_kw):
    records = params.get("records") is True
    before = params.get("before", 0)
    if type(before) is not int or not 0 <= before <= 2**63 - 1:
        return 400, {"error": "Invalid record cursor.", "code": "invalid_notification_cursor"}

    def read(state, current):
        events = state["events"]
        if not _outer(current):
            events = [
                event for event in events if (event["event_type"] in _mandatory(current) or _forced(event, current))
            ]
        next_before = 0
        if records:
            from core.mobile_companion.notification_archive import append, page

            _archive_new(state, current)
            append(current["device_id"], state["events"])
            events, next_before = page(current["device_id"], before=before)
            events = [
                event
                for event in events
                if _record_allowed(event, current)
                and (
                    event["event_type"].startswith("payment_")
                    or event["event_type"] == "task_pending_approval"
                    or _enabled(state["preferences"], event["event_type"], current, event)
                )
            ]
        return {
            "events": [_public(event, state["preferences"], current) for event in events[-200:]],
            "next_before": next_before,
            "badge": sum(
                event["seq"] > state["read_after"]
                for event in state["events"]
                if (_outer(current) or event["event_type"] in _mandatory(current) or _forced(event, current))
            ),
        }

    return 200, service.store.edit_notifications(str(device["device_id"]), read)


def mark_read(_params, *, service, device, **_kw):
    def update(state, _current):
        state["read_after"] = max(state["read_after"], state["delivery_issued"])
        read = [event for event in state["events"] if event["seq"] <= state["read_after"]][-200:]
        unread = [event for event in state["events"] if event["seq"] > state["read_after"]]
        state["events"] = read + unread
        return {"badge": len(unread)}

    return 200, service.store.edit_notifications(str(device["device_id"]), update)
