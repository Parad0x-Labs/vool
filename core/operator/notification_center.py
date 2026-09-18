"""The persistent notification centre: the one history the bell and the native notification bridge read.

Sources write typed items here: a due calendar alert, a delivered chat reminder, and one catch-up notice for
alerts that passed before they could be shown. Surfaces read those typed items. None of them parses assistant
prose to decide whether an event exists or an effect happened.

Law:

* One item per dedupe key (a schedule row's fire generation, a catch-up hour). A replayed sweep, a restarted
  process or two sweepers never add a duplicate.
* Read, dismiss and snooze are user state that survives restart. Dismissing or snoozing an item never
  retracts what a surface already presented, and nothing here claims it does.
* Every channel that carried an item keeps its own delivery state: the in-app feed, the owning chat's
  conversation log, a macOS notification request. Submitting an OS request is not a display, and a display is
  not an acknowledgement.
"""
from __future__ import annotations

import contextlib
import copy
import json
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

SOURCE_KINDS = frozenset({"calendar_alert", "calendar_catch_up", "reminder", "test"})
CHANNELS = frozenset({"in_app", "session_log", "macos"})
DELIVERY_STATES = frozenset({
    "recorded", "queued", "submitted", "listed", "acknowledged", "suppressed", "failed", "uncertain",
    "withdraw_requested", "withdrawn",
})
ACTIONS = frozenset({"read", "open", "dismiss", "snooze"})
SNOOZABLE_KINDS = frozenset({"calendar_alert", "reminder"})
MIN_SNOOZE_MINUTES, MAX_SNOOZE_MINUTES, DEFAULT_SNOOZE_MINUTES = 1, 24 * 60, 10
LIST_CAP = 200
CATCH_UP_TITLE_CAP = 12
HISTORY_CAP = 12
LOCK_SCREEN_MODES = ("title_only", "full", "hidden")
DEFAULT_PREFERENCES: dict[str, Any] = {
    "quiet_hours": {"enabled": False, "start": "22:00", "end": "07:00"},
    "sound": True,
    "lock_screen": "title_only",
    "native_notifications": False,
    "late_grace_minutes": 5,
}
_CLOCK_TEXT = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _utcnow() -> str:
    from core.time_authority import CLOCK

    return CLOCK.now_utc().isoformat()


def _default_connection():
    from storage.db import get_connection

    return get_connection()


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decode(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _item(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["payload"] = _decode(data.pop("payload_json", "{}"))
    data.pop("dedupe_key", None)
    return data


def record_item(
    *,
    dedupe_key: str,
    source_kind: str,
    title: str,
    body: str = "",
    payload: dict[str, Any] | None = None,
    schedule_id: str = "",
    event_key: str = "",
    session_id: str = "",
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Record one typed item once. A second call with the same dedupe key returns the first item unchanged."""
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown notification source kind: {source_kind!r}")
    key = str(dedupe_key or "").strip()
    if not key:
        raise ValueError("a notification item needs a dedupe key")
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO notification_items (
                notification_id, dedupe_key, source_kind, schedule_id, event_key, session_id, title, body,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ntf-" + uuid.uuid4().hex, key, source_kind, str(schedule_id or ""), str(event_key or ""), str(session_id or ""),
             str(title or "")[:300], str(body or "")[:1000], json.dumps(payload or {}, sort_keys=True), now_fn()),
        )
        created = cursor.rowcount == 1
        conn.commit()
        row = conn.execute("SELECT * FROM notification_items WHERE dedupe_key = ?", (key,)).fetchone()
    finally:
        conn.close()
    item = _item(row)
    item["created"] = created
    return item


def supersede_earlier_items(
    schedule_id: str,
    *,
    keep_notification_id: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> int:
    """Hide a schedule's earlier items once a later fire of it (a snoozed alert re-surfacing) has an item."""
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            UPDATE notification_items SET superseded_at = ?
            WHERE schedule_id = ? AND notification_id != ? AND superseded_at IS NULL AND dismissed_at IS NULL
            """,
            (now_fn(), str(schedule_id or ""), str(keep_notification_id or "")),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def supersede_items_for_schedules(
    schedule_ids: list[str],
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> int:
    """Hide open items whose schedule row was superseded or cancelled (a snoozed alert for an event that moved)."""
    ids = [str(value) for value in schedule_ids if str(value or "")]
    if not ids:
        return 0
    conn = get_connection_fn()
    try:
        marks = ",".join("?" for _ in ids)
        cursor = conn.execute(
            f"UPDATE notification_items SET superseded_at = ? WHERE schedule_id IN ({marks}) "
            "AND superseded_at IS NULL AND dismissed_at IS NULL",
            [now_fn(), *ids],
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def record_delivery(
    notification_id: str,
    *,
    channel: str,
    state: str,
    detail: str = "",
    external_id: str = "",
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Record what one channel proved about one item. Each state change is kept in a short history."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown notification channel: {channel!r}")
    if state not in DELIVERY_STATES:
        raise ValueError(f"unknown delivery state: {state!r}")
    delivery_id = f"{notification_id}:{channel}"
    now = now_fn()
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT history_json FROM notification_deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
        history = []
        if row is not None:
            try:
                history = list(json.loads(row["history_json"] or "[]"))
            except (TypeError, json.JSONDecodeError):
                history = []
        history.append({"state": state, "at": now, "detail": str(detail or "")[:200]})
        conn.execute(
            """
            INSERT INTO notification_deliveries (
                delivery_id, notification_id, channel, state, detail, external_id, history_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(delivery_id) DO UPDATE SET
                state = excluded.state,
                detail = excluded.detail,
                external_id = CASE WHEN excluded.external_id != '' THEN excluded.external_id ELSE notification_deliveries.external_id END,
                history_json = excluded.history_json,
                updated_at = excluded.updated_at
            """,
            (delivery_id, notification_id, channel, state, str(detail or "")[:500], str(external_id or ""),
             json.dumps(history[-HISTORY_CAP:]), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"delivery_id": delivery_id, "channel": channel, "state": state}


def list_items(
    *,
    after_seq: int = 0,
    limit: int = 50,
    include_dismissed: bool = False,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Items newest first, the persistent unread count and the feed cursor (the highest sequence recorded).

    By default the list holds open items only: dismissed items and items a later fire superseded are history,
    shown with ``include_dismissed``.
    """
    bounded = max(1, min(int(limit or 50), LIST_CAP))
    where = "seq > ?"
    if not include_dismissed:
        where += " AND dismissed_at IS NULL AND superseded_at IS NULL"
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            f"SELECT * FROM notification_items WHERE {where} ORDER BY seq DESC LIMIT ?",
            (max(0, int(after_seq or 0)), bounded),
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM notification_items WHERE read_at IS NULL AND dismissed_at IS NULL AND superseded_at IS NULL"
        ).fetchone()[0]
        cursor = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM notification_items").fetchone()[0]
        items = [_item(row) for row in rows]
        if items:
            marks = ",".join("?" for _ in items)
            channel_rows = conn.execute(
                f"SELECT notification_id, channel, state, detail, updated_at FROM notification_deliveries WHERE notification_id IN ({marks})",
                [item["notification_id"] for item in items],
            ).fetchall()
            channels: dict[str, dict[str, Any]] = {}
            for channel_row in channel_rows:
                channels.setdefault(channel_row["notification_id"], {})[channel_row["channel"]] = {
                    "state": channel_row["state"], "detail": channel_row["detail"], "updated_at": channel_row["updated_at"],
                }
            for item in items:
                item["channels"] = channels.get(item["notification_id"], {})
    finally:
        conn.close()
    return {"items": items, "unread": int(unread), "cursor": int(cursor)}


def load_item(notification_id: str, *, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any] | None:
    conn = get_connection_fn()
    try:
        row = conn.execute("SELECT * FROM notification_items WHERE notification_id = ?", (str(notification_id or ""),)).fetchone()
    finally:
        conn.close()
    return _item(row) if row is not None else None


def apply_action(
    notification_id: str,
    *,
    action: str,
    minutes: Any = None,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Apply one user action. ``status`` is the HTTP status the served route answers with."""
    if action not in ACTIONS:
        return {"ok": False, "status": 400, "reason": "unknown_action", "detail": f"actions are {', '.join(sorted(ACTIONS))}"}
    item = load_item(notification_id, get_connection_fn=get_connection_fn)
    if item is None:
        return {"ok": False, "status": 404, "reason": "unknown_notification"}
    now = now_fn()
    if action == "snooze":
        return _snooze(item, minutes=minutes, now=now, now_fn=now_fn, get_connection_fn=get_connection_fn)
    conn = get_connection_fn()
    try:
        if action == "dismiss":
            conn.execute(
                """
                UPDATE notification_items
                SET read_at = COALESCE(read_at, ?), dismissed_at = COALESCE(dismissed_at, ?), last_action = 'dismiss', last_action_at = ?
                WHERE notification_id = ?
                """,
                (now, now, now, item["notification_id"]),
            )
        else:
            conn.execute(
                "UPDATE notification_items SET read_at = COALESCE(read_at, ?), last_action = ?, last_action_at = ? WHERE notification_id = ?",
                (now, action, now, item["notification_id"]),
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "status": 200, "notification_id": item["notification_id"], "action": action}


def _snooze(item: dict[str, Any], *, minutes: Any, now: str, now_fn: Callable[[], str], get_connection_fn: Callable[[], Any]) -> dict[str, Any]:
    if item["source_kind"] not in SNOOZABLE_KINDS or not item.get("schedule_id"):
        return {"ok": False, "status": 409, "reason": "not_snoozable", "detail": "only a calendar alert or a reminder can be snoozed"}
    if item.get("dismissed_at") or item.get("superseded_at"):
        return {"ok": False, "status": 409, "reason": "already_closed", "detail": "this notification was already dismissed or replaced"}
    try:
        span = DEFAULT_SNOOZE_MINUTES if minutes in (None, "") else int(minutes)
    except (TypeError, ValueError):
        return {"ok": False, "status": 400, "reason": "invalid_minutes"}
    if isinstance(minutes, bool) or not MIN_SNOOZE_MINUTES <= span <= MAX_SNOOZE_MINUTES:
        return {"ok": False, "status": 400, "reason": "invalid_minutes",
                "detail": f"snooze between {MIN_SNOOZE_MINUTES} and {MAX_SNOOZE_MINUTES} minutes"}
    start = _parse(now) or datetime.now(timezone.utc)
    until = (start + timedelta(minutes=span)).isoformat()
    from core.operator import reminders

    moved = reminders.snooze_schedule(item["schedule_id"], until_utc=until, now_fn=now_fn, get_connection_fn=get_connection_fn)
    if not moved.get("ok"):
        return {"ok": False, "status": 409, "reason": str(moved.get("reason") or "not_snoozable"), "detail": str(moved.get("detail") or "")}
    conn = get_connection_fn()
    try:
        conn.execute(
            """
            UPDATE notification_items
            SET read_at = COALESCE(read_at, ?), snoozed_until = ?, last_action = 'snooze', last_action_at = ?
            WHERE notification_id = ?
            """,
            (now, until, now, item["notification_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "status": 200, "notification_id": item["notification_id"], "action": "snooze", "snoozed_until": until}


def record_catch_up(
    rows: list[dict[str, Any]],
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any] | None:
    """Collect alerts that came due after their event started into ONE notice per hour, not one item each."""
    titles = []
    for row in rows:
        payload = _decode(row.get("payload_json"))
        titles.append(str(payload.get("title") or row.get("note") or "(untitled event)")[:120])
    if not titles:
        return None
    now = now_fn()
    bucket = (_parse(now) or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H")
    key = f"calendar_catch_up:{bucket}"
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload_json FROM notification_items WHERE dedupe_key = ?", (key,)).fetchone()
        previous = _decode(row["payload_json"]) if row is not None else {}
        total = int(previous.get("missed") or 0) + len(titles)
        kept = (list(previous.get("titles") or []) + titles)[:CATCH_UP_TITLE_CAP]
        payload = {"missed": total, "titles": kept, "more": max(0, total - len(kept))}
        title = f"{total} calendar alert{'s' if total != 1 else ''} passed before {'they' if total != 1 else 'it'} could be shown"
        body = "; ".join(kept) + (f" and {payload['more']} more" if payload["more"] else "")
        if row is None:
            conn.execute(
                """
                INSERT INTO notification_items (notification_id, dedupe_key, source_kind, title, body, payload_json, created_at)
                VALUES (?, ?, 'calendar_catch_up', ?, ?, ?, ?)
                """,
                ("ntf-" + uuid.uuid4().hex, key, title, body[:1000], json.dumps(payload, sort_keys=True), now),
            )
        else:
            conn.execute(
                "UPDATE notification_items SET title = ?, body = ?, payload_json = ?, read_at = NULL WHERE dedupe_key = ?",
                (title, body[:1000], json.dumps(payload, sort_keys=True), key),
            )
        conn.commit()
        stored = conn.execute("SELECT * FROM notification_items WHERE dedupe_key = ?", (key,)).fetchone()
    finally:
        conn.close()
    item = _item(stored)
    record_delivery(item["notification_id"], channel="in_app", state="recorded", now_fn=now_fn, get_connection_fn=get_connection_fn)
    return item


def unread_count(*, get_connection_fn: Callable[[], Any] = _default_connection) -> int:
    conn = get_connection_fn()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM notification_items WHERE read_at IS NULL AND dismissed_at IS NULL AND superseded_at IS NULL"
        ).fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------------------------
# Preferences: explicit, validated, persisted
# ---------------------------------------------------------------------------------------------


def load_preferences(*, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    preferences = copy.deepcopy(DEFAULT_PREFERENCES)
    conn = get_connection_fn()
    try:
        row = conn.execute("SELECT value_json FROM notification_preferences WHERE pref_key = 'global'").fetchone()
    finally:
        conn.close()
    stored = _decode(row["value_json"]) if row is not None else {}
    with contextlib.suppress(ValueError):  # a damaged stored value never replaces the defaults with a guess
        preferences.update(_validated_preferences(stored, base=preferences))
    return preferences


def save_preferences(
    patch: dict[str, Any],
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    if not isinstance(patch, dict):
        return {"ok": False, "reason": "invalid_preferences", "detail": "preferences must be an object"}
    current = load_preferences(get_connection_fn=get_connection_fn)
    before = copy.deepcopy(current)
    try:
        current.update(_validated_preferences(patch, base=current))
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_preferences", "detail": str(exc)}
    conn = get_connection_fn()
    try:
        conn.execute(
            """
            INSERT INTO notification_preferences (pref_key, value_json, updated_at) VALUES ('global', ?, ?)
            ON CONFLICT(pref_key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (json.dumps(current, sort_keys=True), now_fn()),
        )
        conn.commit()
    finally:
        conn.close()
    if bool(before.get("native_notifications")) != bool(current.get("native_notifications")):
        from core.operator import native_notifications

        native_notifications.preferences_changed(before, current, now_fn=now_fn, get_connection_fn=get_connection_fn)
    return {"ok": True, "preferences": current}


def _validated_preferences(values: dict[str, Any], *, base: dict[str, Any]) -> dict[str, Any]:
    accepted: dict[str, Any] = {}
    for key, value in values.items():
        if key not in DEFAULT_PREFERENCES:
            raise ValueError(f"unknown preference {key!r}")
        if key == "quiet_hours":
            if not isinstance(value, dict):
                raise ValueError("quiet_hours must be an object")
            merged = dict(base.get("quiet_hours") or DEFAULT_PREFERENCES["quiet_hours"])
            for part, part_value in value.items():
                if part == "enabled":
                    if not isinstance(part_value, bool):
                        raise ValueError("quiet_hours.enabled must be true or false")
                elif part in {"start", "end"}:
                    if not isinstance(part_value, str) or not _CLOCK_TEXT.match(part_value):
                        raise ValueError(f"quiet_hours.{part} must be HH:MM")
                else:
                    raise ValueError(f"unknown quiet_hours field {part!r}")
                merged[part] = part_value
            accepted[key] = merged
        elif key in {"sound", "native_notifications"}:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            accepted[key] = value
        elif key == "lock_screen":
            if value not in LOCK_SCREEN_MODES:
                raise ValueError(f"lock_screen must be one of {', '.join(LOCK_SCREEN_MODES)}")
            accepted[key] = value
        elif key == "late_grace_minutes":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 60:
                raise ValueError("late_grace_minutes must be a whole number from 0 to 60")
            accepted[key] = value
    return accepted


def in_quiet_hours(preferences: dict[str, Any], *, local_now: datetime) -> bool:
    """Whether a local wall time falls in the user's quiet hours (a window may cross midnight)."""
    quiet = preferences.get("quiet_hours") or {}
    if not quiet.get("enabled"):
        return False
    try:
        start_h, start_m = (int(part) for part in str(quiet.get("start")).split(":"))
        end_h, end_m = (int(part) for part in str(quiet.get("end")).split(":"))
    except ValueError:
        return False
    minute = local_now.hour * 60 + local_now.minute
    start, end = start_h * 60 + start_m, end_h * 60 + end_m
    if start == end:
        return False
    return start <= minute < end if start < end else (minute >= start or minute < end)


__all__ = [
    "ACTIONS", "CHANNELS", "DEFAULT_PREFERENCES", "DELIVERY_STATES", "SOURCE_KINDS", "apply_action", "in_quiet_hours",
    "list_items", "load_item", "load_preferences", "record_catch_up", "record_delivery", "record_item", "save_preferences",
    "supersede_earlier_items", "supersede_items_for_schedules", "unread_count",
]
