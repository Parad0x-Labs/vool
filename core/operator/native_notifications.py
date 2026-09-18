"""The macOS notification channel on the runtime's side: one outbox of OS notification requests, and what the bridge
reported about each.

The bell (the notification centre) is the record of every alert. macOS notifications are an extra copy the person
turns on separately. VOOL chooses every request identifier, so a request handed twice replaces itself in macOS instead
of adding a second notification:

* ``vool.s.<schedule id>.<fire generation>.<due instant>`` -- a request scheduled ahead for an alert or reminder due
  within the next 12 hours, so it can appear while VOOL is closed;
* ``vool.n.<notification id>`` -- a request for a bell item that had none scheduled ahead (it came due within the
  minute, it is a catch-up notice, or it is a test).

The window host's bridge takes the outbox (``POST /api/notifications/native/outbox``), hands it to the Swift helper
(core/notifications_macos.py) and posts back what the helper reports (``POST /api/notifications/native/report``). The
states stay separate: queued (handed to the bridge), submitted (macOS accepted the request), listed (macOS lists it
among its delivered notifications), acknowledged (the person clicked it or chose an action), suppressed (VOOL did not
send it, with the reason), failed, withdraw_requested and withdrawn. Nothing here records that a banner was displayed:
macOS does not report that, and Focus can hold back a notification macOS accepted.

A request is withdrawn when its alert moves, is snoozed or cancelled, when its bell item is dismissed, when the account
is disconnected and when the channel is turned off. macOS removes a pending request and takes a delivered one out of
Notification Center; a notification already on screen cannot be taken back.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

BRIDGE_STATE_KEY = "native_bridge"
SCHEDULE_AHEAD_HOURS = 12
SCHEDULE_MIN_LEAD_SECONDS = 60
IMMEDIATE_WINDOW_MINUTES = 30
REHAND_AFTER_SECONDS = 60
MAX_HANDS = 3
CONNECTED_WITHIN_SECONDS = 60
#: How long a permission request macOS has not answered counts as waiting (its prompt may be on screen).
AUTHORIZATION_PENDING_MINUTES = 10
OUTBOX_CAP = 32
REPORT_EVENT_CAP = 200
UNKNOWN_CAP = 50
STATUS_ROWS = 20
_SQL_CHUNK = 400
IDENTIFIER_PREFIX = "vool."
NATIVE_KINDS = ("calendar_alert", "calendar_catch_up", "reminder", "test")
AUTHORIZATIONS = ("not_determined", "denied", "authorized", "provisional", "ephemeral")
SENDABLE = frozenset({"authorized", "provisional", "ephemeral"})
SETTINGS_KEYS = ("authorization", "alert", "sound", "notification_center", "lock_screen", "previews", "alert_style")
REQUEST_STATES = ("queued", "submitted", "listed", "acknowledged", "suppressed", "failed", "withdraw_requested", "withdrawn")
RESPONSE_ACTIONS = ("open", "snooze", "dismiss")
TEST_TITLE = "VOOL test notification"
TEST_BODY = "If this appears, macOS accepted a notification from VOOL."
GENERIC_BODY = {
    "calendar_alert": "A calendar alert is due",
    "calendar_catch_up": "Calendar alerts passed while VOOL was not running",
    "reminder": "A reminder is due",
    "test": "Test notification",
}
#: What the macOS channel is and is not, shown beside its settings.
EXPLANATION = (
    "The bell keeps every VOOL alert. macOS notifications are an extra copy you turn on here; upcoming alerts are handed "
    "to macOS ahead of time, so they can appear while VOOL is closed. VOOL records when macOS accepted a notification, "
    "when macOS lists it in Notification Center and when you click it. macOS does not say whether a banner was shown, "
    "and Focus can hold one back. VOOL alerts are separate from the alarms your calendar provider sends itself: VOOL "
    "never changes those, so with both on you can get one alert from each."
)

_INSERT = """
INSERT INTO native_notification_requests (
    identifier, kind, notification_id, schedule_id, fire_generation, deliver_at_utc, state, detail, request_json,
    hand_count, handed_at, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


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


def _user_zone() -> ZoneInfo:
    try:
        from core.user_preferences import load_user_timezone

        return ZoneInfo(str(load_user_timezone() or "").strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _local_clock(value: Any) -> str:
    parsed = _parse(value)
    return parsed.astimezone(_user_zone()).strftime("%H:%M") if parsed is not None else ""


def _load_state(conn: Any) -> dict[str, Any]:
    row = conn.execute("SELECT value_json FROM notification_preferences WHERE pref_key = ?", (BRIDGE_STATE_KEY,)).fetchone()
    stored = _decode(row["value_json"]) if row is not None else {}
    state: dict[str, Any] = {
        "enabled_at": "", "want_authorization": False, "settings": {}, "settings_at": "", "seen_at": "", "bridge_id": "",
        "helper_version": "", "unknown_identifiers": [], "authorization_request": {},
    }
    for key in state:
        if key in stored:
            state[key] = stored[key]
    if not isinstance(state["settings"], dict):
        state["settings"] = {}
    if not isinstance(state["unknown_identifiers"], list):
        state["unknown_identifiers"] = []
    if not isinstance(state["authorization_request"], dict):
        state["authorization_request"] = {}
    return state


def _save_state(conn: Any, state: dict[str, Any], now_iso: str) -> None:
    conn.execute(
        """
        INSERT INTO notification_preferences (pref_key, value_json, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(pref_key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (BRIDGE_STATE_KEY, json.dumps(state, sort_keys=True), now_iso),
    )


def _content(source_kind: str, title: str, body: str, preferences: dict[str, Any]) -> tuple[str, str]:
    """What the request carries, by the person's lock-screen text choice: full, the title only, or nothing personal."""
    mode = str(preferences.get("lock_screen") or "title_only")
    if mode == "full":
        return title, body
    if mode == "hidden":
        return "VOOL", GENERIC_BODY.get(source_kind, "VOOL notification")
    return title, ""


def _submit_op(*, identifier: str, kind: str, deliver_at: str, source_kind: str, title: str, body: str,
               preferences: dict[str, Any], thread: str, notification_id: str = "", schedule_id: str = "") -> dict[str, Any]:
    shown_title, shown_body = _content(source_kind, title, body, preferences)
    return {
        "op": "submit", "identifier": identifier, "kind": kind, "deliver_at_utc": deliver_at,
        "title": shown_title[:200], "body": shown_body[:400], "sound": bool(preferences.get("sound", True)),
        "category": "vool.alert" if source_kind in ("calendar_alert", "reminder") else "vool.info",
        "thread": str(thread or "vool")[:120], "source_kind": source_kind, "notification_id": notification_id,
        "schedule_id": schedule_id,
    }


def _generation(dedupe_key: str) -> int | None:
    """The fire generation a calendar alert or reminder item was recorded for (its dedupe key ends with it)."""
    key = str(dedupe_key or "")
    if not key.startswith(("calendar_alert:", "reminder:")):
        return None
    tail = key.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def outbox(
    *,
    bridge_id: str = "",
    helper_version: str = "",
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """The requests the bridge should hand to macOS now: withdrawals first, then re-sends, then new requests."""
    from core.operator import notification_center

    now_iso = now_fn()
    now = _parse(now_iso) or datetime.now(timezone.utc)
    preferences = notification_center.load_preferences(get_connection_fn=get_connection_fn)
    enabled = bool(preferences.get("native_notifications"))
    ops: list[dict[str, Any]] = []
    effects: list[tuple[Any, ...]] = []
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = _load_state(conn)
        state.update(seen_at=now_iso, bridge_id=str(bridge_id or "")[:80], helper_version=str(helper_version or "")[:40])
        authorization = str(state["settings"].get("authorization") or "")
        for identifier in [str(value) for value in state["unknown_identifiers"]][:OUTBOX_CAP]:
            ops.append({"op": "withdraw", "identifier": identifier, "reason": "macOS holds a VOOL request this store does not know"})
        state["unknown_identifiers"] = []
        _withdrawals(conn, now=now, now_iso=now_iso, enabled=enabled, ops=ops, effects=effects)
        if enabled and authorization in SENDABLE:
            _rehands(conn, now=now, now_iso=now_iso, ops=ops, effects=effects)
        if enabled and authorization:
            _immediate(conn, now=now, now_iso=now_iso, state=state, authorization=authorization, preferences=preferences,
                       ops=ops, effects=effects)
            if authorization in SENDABLE:
                _scheduled(conn, now=now, now_iso=now_iso, preferences=preferences, ops=ops)
        _save_state(conn, state, now_iso)
        conn.commit()
    except Exception:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        conn.close()
    _apply_effects(effects, now_fn=now_fn, get_connection_fn=get_connection_fn)
    return {
        "ok": True,
        "enabled": enabled,
        "authorization": authorization or "unknown",
        "want_settings": not authorization,
        "want_authorization": bool(enabled and state["want_authorization"] and authorization not in SENDABLE
                                   and not _authorization_pending(state, now)),
        "requests": ops,
    }


def _withdrawals(conn: Any, *, now: datetime, now_iso: str, enabled: bool, ops: list[dict[str, Any]],
                 effects: list[tuple[Any, ...]]) -> None:
    rows = conn.execute(
        """
        SELECT r.*, q.status AS schedule_status, q.fire_generation AS schedule_generation, q.due_at_utc AS schedule_due,
               i.dismissed_at AS item_dismissed_at, i.superseded_at AS item_superseded_at
        FROM native_notification_requests r
        LEFT JOIN reminder_requests q ON r.kind = 'scheduled' AND q.reminder_id = r.schedule_id
        LEFT JOIN notification_items i ON r.notification_id != '' AND i.notification_id = r.notification_id
        WHERE r.state IN ('queued', 'submitted', 'listed', 'withdraw_requested')
        ORDER BY r.created_at ASC
        """
    ).fetchall()
    for row in rows:
        if len(ops) >= OUTBOX_CAP:
            return
        if row["state"] == "withdraw_requested":
            # Handed again only when a listing showed macOS still holds it (handed_at cleared), a bounded number of times.
            if not row["handed_at"] and int(row["hand_count"] or 0) < MAX_HANDS:
                ops.append({"op": "withdraw", "identifier": row["identifier"], "reason": row["detail"]})
                conn.execute(
                    "UPDATE native_notification_requests SET handed_at = ?, hand_count = hand_count + 1, updated_at = ? WHERE identifier = ?",
                    (now_iso, now_iso, row["identifier"]),
                )
            continue
        reason = _withdraw_reason(row, now=now, enabled=enabled)
        if not reason:
            continue
        ops.append({"op": "withdraw", "identifier": row["identifier"], "reason": reason})
        conn.execute(
            """
            UPDATE native_notification_requests SET state = 'withdraw_requested', detail = ?, handed_at = ?, hand_count = 1,
                updated_at = ? WHERE identifier = ?
            """,
            (reason, now_iso, now_iso, row["identifier"]),
        )
        if row["notification_id"]:
            effects.append(("delivery", row["notification_id"], "withdraw_requested", reason, row["identifier"]))


def _withdraw_reason(row: Any, *, now: datetime, enabled: bool) -> str:
    dismissed = bool(row["item_dismissed_at"] or row["item_superseded_at"])
    if row["kind"] == "scheduled":
        deliver_at = _parse(row["deliver_at_utc"])
        if not enabled and deliver_at is not None and deliver_at > now:
            return "macOS notifications were turned off in VOOL"
        status = row["schedule_status"]
        if status is None:
            return "the alert no longer exists"
        if status == "scheduled" and (
            int(row["schedule_generation"] or 0) != int(row["fire_generation"] or 0)
            or _parse(row["schedule_due"]) != deliver_at
        ):
            return "the alert moved or was snoozed"
        if status == "cancelled":
            return "the alert was cancelled"
        if dismissed and row["state"] in ("submitted", "listed"):
            return "dismissed in VOOL"
        return ""
    if not enabled and row["state"] == "queued":
        return "macOS notifications were turned off in VOOL"
    if dismissed and row["state"] in ("submitted", "listed"):
        return "dismissed in VOOL"
    return ""


def _rehands(conn: Any, *, now: datetime, now_iso: str, ops: list[dict[str, Any]], effects: list[tuple[Any, ...]]) -> None:
    cutoff = (now - timedelta(seconds=REHAND_AFTER_SECONDS)).isoformat()
    rows = conn.execute(
        "SELECT * FROM native_notification_requests WHERE state = 'queued' AND handed_at != '' AND handed_at <= ? ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    for row in rows:
        if len(ops) >= OUTBOX_CAP:
            return
        deliver_at = _parse(row["deliver_at_utc"])
        late = row["kind"] == "scheduled" and deliver_at is not None and deliver_at <= now
        detail = ""
        if late:
            detail = "the bridge never confirmed the request before its time"
        elif int(row["hand_count"] or 0) >= MAX_HANDS:
            detail = f"the bridge never confirmed the request after {MAX_HANDS} tries"
        op = _decode(row["request_json"])
        if not detail and not op:
            detail = "the stored request is unreadable"
        if detail:
            conn.execute(
                "UPDATE native_notification_requests SET state = 'failed', detail = ?, updated_at = ? WHERE identifier = ?",
                (detail, now_iso, row["identifier"]),
            )
            if row["notification_id"]:
                effects.append(("delivery", row["notification_id"], "failed", detail, row["identifier"]))
            continue
        ops.append(op)
        conn.execute(
            "UPDATE native_notification_requests SET handed_at = ?, hand_count = hand_count + 1, updated_at = ? WHERE identifier = ?",
            (now_iso, now_iso, row["identifier"]),
        )


def _immediate(conn: Any, *, now: datetime, now_iso: str, state: dict[str, Any], authorization: str,
               preferences: dict[str, Any], ops: list[dict[str, Any]], effects: list[tuple[Any, ...]]) -> None:
    from core.operator import notification_center

    if not state["enabled_at"]:
        state["enabled_at"] = now_iso
    enabled_at = _parse(state["enabled_at"]) or now
    cutoff = max(enabled_at, now - timedelta(minutes=IMMEDIATE_WINDOW_MINUTES))
    marks = ",".join("?" for _ in NATIVE_KINDS)
    rows = conn.execute(
        f"""
        SELECT * FROM notification_items i
        WHERE i.created_at >= ? AND i.dismissed_at IS NULL AND i.superseded_at IS NULL AND i.source_kind IN ({marks})
          AND NOT EXISTS (SELECT 1 FROM native_notification_requests r WHERE r.notification_id = i.notification_id)
        ORDER BY i.seq ASC LIMIT ?
        """,
        (cutoff.astimezone(timezone.utc).isoformat(), *NATIVE_KINDS, OUTBOX_CAP),
    ).fetchall()
    local_now = now.astimezone(_user_zone())
    for row in rows:
        if len(ops) >= OUTBOX_CAP:
            return
        notification_id, source_kind = row["notification_id"], row["source_kind"]
        generation = _generation(row["dedupe_key"])
        if row["schedule_id"] and generation is not None:
            linked = conn.execute(
                """
                SELECT identifier, state FROM native_notification_requests
                WHERE kind = 'scheduled' AND schedule_id = ? AND fire_generation = ? AND state IN ('queued', 'submitted', 'listed', 'acknowledged')
                ORDER BY created_at DESC LIMIT 1
                """,
                (row["schedule_id"], generation),
            ).fetchone()
            if linked is not None:
                conn.execute(
                    "UPDATE native_notification_requests SET notification_id = ?, updated_at = ? WHERE identifier = ?",
                    (notification_id, now_iso, linked["identifier"]),
                )
                effects.append(("delivery", notification_id, linked["state"], "macOS holds the request scheduled ahead for this alert",
                                linked["identifier"]))
                continue
        identifier = f"vool.n.{notification_id}"
        suppressed = ""
        if authorization not in SENDABLE:
            suppressed = ("macOS permission for VOOL notifications is denied" if authorization == "denied"
                          else "macOS permission for VOOL notifications has not been granted yet")
        elif source_kind != "test" and notification_center.in_quiet_hours(preferences, local_now=local_now):
            suppressed = "quiet hours: the bell has it and macOS was not asked"
        if suppressed:
            conn.execute(_INSERT, (identifier, "immediate", notification_id, row["schedule_id"], generation or 0, "", "suppressed",
                                   suppressed, "", 0, "", now_iso, now_iso))
            effects.append(("delivery", notification_id, "suppressed", suppressed, identifier))
            continue
        op = _submit_op(identifier=identifier, kind="immediate", deliver_at="", source_kind=source_kind, title=row["title"],
                        body=row["body"], preferences=preferences, thread=row["event_key"] or f"vool.{source_kind}",
                        notification_id=notification_id, schedule_id=row["schedule_id"])
        conn.execute(_INSERT, (identifier, "immediate", notification_id, row["schedule_id"], generation or 0, "", "queued",
                               "handed to the macOS bridge", json.dumps(op, sort_keys=True), 1, now_iso, now_iso, now_iso))
        effects.append(("delivery", notification_id, "queued", "handed to the macOS bridge", identifier))
        ops.append(op)


def _scheduled(conn: Any, *, now: datetime, now_iso: str, preferences: dict[str, Any], ops: list[dict[str, Any]]) -> None:
    from core.operator import notification_center

    rows = conn.execute(
        """
        SELECT q.* FROM reminder_requests q
        WHERE q.status = 'scheduled' AND q.source_kind IN ('calendar_alert', 'reminder')
          AND q.due_at_utc > ? AND q.due_at_utc <= ?
          AND NOT EXISTS (
              SELECT 1 FROM native_notification_requests r
              WHERE r.kind = 'scheduled' AND r.schedule_id = q.reminder_id AND r.fire_generation = q.fire_generation
                AND r.deliver_at_utc = q.due_at_utc
          )
        ORDER BY q.due_at_utc ASC LIMIT ?
        """,
        ((now + timedelta(seconds=SCHEDULE_MIN_LEAD_SECONDS)).isoformat(), (now + timedelta(hours=SCHEDULE_AHEAD_HOURS)).isoformat(),
         OUTBOX_CAP),
    ).fetchall()
    zone = _user_zone()
    for row in rows:
        if len(ops) >= OUTBOX_CAP:
            return
        due = _parse(row["due_at_utc"])
        if due is None or notification_center.in_quiet_hours(preferences, local_now=due.astimezone(zone)):
            continue  # inside quiet hours: the bell gets it when it comes due and records why macOS was skipped
        payload = _decode(row["payload_json"])
        source_kind = row["source_kind"]
        if source_kind == "calendar_alert":
            title = str(payload.get("title") or row["note"] or "(untitled event)")
            parts = [f"Starts at {_local_clock(payload.get('start_utc'))}"] if payload.get("start_utc") else []
            parts += [str(payload[key]) for key in ("calendar_name", "location") if payload.get(key)]
            body, thread = " · ".join(parts), str(row["source_ref"] or "vool.calendar_alert")
        else:
            title, body, thread = f"Reminder: {row['note']}", f"Due at {_local_clock(row['due_at_utc'])}", "vool.reminder"
        generation = int(row["fire_generation"] or 0)
        due_utc = due.astimezone(timezone.utc)
        identifier = f"vool.s.{row['reminder_id']}.{generation}.{due_utc:%Y%m%dT%H%M%SZ}"
        op = _submit_op(identifier=identifier, kind="scheduled", deliver_at=due_utc.isoformat(), source_kind=source_kind, title=title,
                        body=body, preferences=preferences, thread=thread, schedule_id=row["reminder_id"])
        conn.execute(_INSERT, (identifier, "scheduled", "", row["reminder_id"], generation, row["due_at_utc"], "queued",
                               "handed to the macOS bridge", json.dumps(op, sort_keys=True), 1, now_iso, now_iso, now_iso))
        ops.append(op)


def ingest_report(
    *,
    bridge_id: str = "",
    events: Any,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Apply what the bridge reported: macOS settings, request outcomes, macOS's listing and the person's responses."""
    if not isinstance(events, list):
        return {"ok": False, "reason": "invalid_report", "detail": "events must be a list"}
    now_iso = now_fn()
    effects: list[tuple[Any, ...]] = []
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = _load_state(conn)
        state["seen_at"] = now_iso
        if bridge_id:
            state["bridge_id"] = str(bridge_id)[:80]
        for event in events[:REPORT_EVENT_CAP]:
            if not isinstance(event, dict):
                continue
            name = str(event.get("event") or "")
            if name == "settings":
                _ingest_settings(state, event, now_iso)
            elif name in ("submitted", "failed", "already_listed", "withdraw_requested"):
                _ingest_request_state(conn, name, event, now_iso=now_iso, effects=effects)
            elif name == "listing":
                _ingest_listing(conn, state, event, now_iso=now_iso, effects=effects)
            elif name in ("authorization_requested", "authorization_result"):
                _ingest_authorization(state, name, event, now_iso)
            elif name == "response":
                _ingest_response(conn, event, now_iso=now_iso, effects=effects)
        _save_state(conn, state, now_iso)
        conn.commit()
    except Exception:
        with contextlib.suppress(Exception):
            conn.rollback()
        raise
    finally:
        conn.close()
    applied, opened = _apply_effects(effects, now_fn=now_fn, get_connection_fn=get_connection_fn)
    return {"ok": True, "applied": applied, "opened": opened}


def _ingest_settings(state: dict[str, Any], event: dict[str, Any], now_iso: str) -> None:
    settings = {key: str(event.get(key) or "")[:40] for key in SETTINGS_KEYS}
    if settings["authorization"] not in AUTHORIZATIONS:
        settings["authorization"] = ""  # a name this code does not know is never read as permission
    state["settings"], state["settings_at"] = settings, now_iso
    if settings["authorization"] in SENDABLE or settings["authorization"] == "denied":
        state["want_authorization"] = False


def _authorization_pending(state: dict[str, Any], now: datetime) -> bool:
    """A permission request the helper made that macOS has not answered, for up to ten minutes."""
    request = state.get("authorization_request") or {}
    requested = _parse(request.get("requested_at"))
    if requested is None:
        return False
    answered = _parse(request.get("answered_at"))
    if answered is not None and answered >= requested:
        return False
    return now - requested < timedelta(minutes=AUTHORIZATION_PENDING_MINUTES)


def _ingest_authorization(state: dict[str, Any], name: str, event: dict[str, Any], now_iso: str) -> None:
    """The helper asked macOS for permission, or macOS answered. An answer ends the request: VOOL does not ask again
    until the person presses the button."""
    request = dict(state.get("authorization_request") or {})
    if name == "authorization_requested":
        request["requested_at"] = now_iso
    else:
        granted, code = event.get("granted"), event.get("error_code")
        request.update(
            answered_at=now_iso,
            granted=granted if isinstance(granted, bool) else None,
            error_domain=str(event.get("error_domain") or "")[:80],
            error_code=code if isinstance(code, int) and not isinstance(code, bool) else None,
            detail=str(event.get("detail") or "")[:200],
        )
        state["want_authorization"] = False
    state["authorization_request"] = request


def _recovery(*, enabled: bool, authorization: str, state: dict[str, Any], now: datetime | None) -> str:
    """The one next step Settings shows for the macOS channel, or \"\" when nothing is needed."""
    if not enabled or authorization in SENDABLE:
        return ""
    if authorization == "denied":
        return ("macOS has notifications for VOOL Notifications turned off. Turn them on in System Settings > Notifications > "
                "VOOL Notifications; until then the bell keeps every alert.")
    if now is not None and _authorization_pending(state, now):
        return "macOS is asking whether VOOL Notifications may send notifications; answer its prompt to finish."
    request = state.get("authorization_request") or {}
    if request.get("answered_at") and request.get("granted") is False:
        reason = f" ({request['detail']})" if request.get("detail") else ""
        return ("macOS did not grant permission" + reason + ". Press Ask macOS for permission to try again; if no prompt "
                "appears, turn on notifications for VOOL Notifications in System Settings > Notifications.")
    if not authorization:
        return "Waiting for the VOOL app: the VOOL window hands notifications to macOS while it runs."
    return "Press Ask macOS for permission; macOS then asks you once."


def _ingest_request_state(conn: Any, name: str, event: dict[str, Any], *, now_iso: str, effects: list[tuple[Any, ...]]) -> None:
    identifier = str(event.get("identifier") or "")
    row = conn.execute("SELECT * FROM native_notification_requests WHERE identifier = ?", (identifier,)).fetchone()
    if row is None:
        return
    nid = row["notification_id"]
    if name == "submitted":
        if row["state"] in ("queued", "failed"):
            conn.execute(
                "UPDATE native_notification_requests SET state = 'submitted', detail = '', submitted_at = ?, updated_at = ? WHERE identifier = ?",
                (now_iso, now_iso, identifier),
            )
            if nid:
                effects.append(("delivery", nid, "submitted", "macOS accepted the request", identifier))
        elif row["state"] in ("withdraw_requested", "withdrawn"):
            # macOS accepted a request VOOL already decided to withdraw: withdraw it again.
            conn.execute(
                "UPDATE native_notification_requests SET state = 'withdraw_requested', handed_at = '', updated_at = ? WHERE identifier = ?",
                (now_iso, identifier),
            )
    elif name == "already_listed":
        if row["state"] in ("queued", "submitted"):
            conn.execute(
                "UPDATE native_notification_requests SET state = 'listed', listed_at = ?, updated_at = ? WHERE identifier = ?",
                (now_iso, now_iso, identifier),
            )
            if nid:
                effects.append(("delivery", nid, "listed", "macOS lists it in Notification Center", identifier))
    elif name == "failed":
        if row["state"] in ("queued", "submitted"):
            detail = str(event.get("detail") or "macOS refused the request")[:200]
            conn.execute(
                "UPDATE native_notification_requests SET state = 'failed', detail = ?, updated_at = ? WHERE identifier = ?",
                (detail, now_iso, identifier),
            )
            if nid:
                effects.append(("delivery", nid, "failed", detail, identifier))


def _ingest_listing(conn: Any, state: dict[str, Any], event: dict[str, Any], *, now_iso: str, effects: list[tuple[Any, ...]]) -> None:
    pending = {value for value in event.get("pending") or [] if isinstance(value, str)}
    delivered = {value for value in event.get("delivered") or [] if isinstance(value, str)}
    ours = sorted(value for value in pending | delivered if value.startswith(IDENTIFIER_PREFIX))
    held = set(ours)
    known: set[str] = set()
    for index in range(0, len(ours), _SQL_CHUNK):
        chunk = ours[index:index + _SQL_CHUNK]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"SELECT identifier, state, notification_id FROM native_notification_requests WHERE identifier IN ({marks})", chunk
        ).fetchall():
            identifier = row["identifier"]
            known.add(identifier)
            if identifier in delivered and row["state"] in ("queued", "submitted"):
                conn.execute(
                    "UPDATE native_notification_requests SET state = 'listed', listed_at = ?, updated_at = ? WHERE identifier = ?",
                    (now_iso, now_iso, identifier),
                )
                if row["notification_id"]:
                    effects.append(("delivery", row["notification_id"], "listed", "macOS lists it in Notification Center", identifier))
            elif identifier in pending and row["state"] == "queued":
                conn.execute(
                    "UPDATE native_notification_requests SET state = 'submitted', submitted_at = ?, updated_at = ? WHERE identifier = ?",
                    (now_iso, now_iso, identifier),
                )
                if row["notification_id"]:
                    effects.append(("delivery", row["notification_id"], "submitted", "macOS holds the request", identifier))
            elif row["state"] == "withdraw_requested":
                conn.execute(
                    "UPDATE native_notification_requests SET handed_at = '', updated_at = ? WHERE identifier = ?", (now_iso, identifier),
                )
    for row in conn.execute(
        "SELECT identifier, notification_id FROM native_notification_requests WHERE state = 'withdraw_requested'"
    ).fetchall():
        if row["identifier"] in held:
            continue
        conn.execute(
            "UPDATE native_notification_requests SET state = 'withdrawn', updated_at = ? WHERE identifier = ?", (now_iso, row["identifier"]),
        )
        if row["notification_id"]:
            effects.append(("delivery", row["notification_id"], "withdrawn", "macOS no longer holds it", row["identifier"]))
    state["unknown_identifiers"] = sorted(held - known)[:UNKNOWN_CAP]


def _ingest_response(conn: Any, event: dict[str, Any], *, now_iso: str, effects: list[tuple[Any, ...]]) -> None:
    identifier = str(event.get("identifier") or "")
    action = str(event.get("action") or "open")
    if action not in RESPONSE_ACTIONS:
        action = "open"
    row = conn.execute("SELECT * FROM native_notification_requests WHERE identifier = ?", (identifier,)).fetchone()
    if row is None or (row["state"] == "acknowledged" and row["last_action"] == action):
        return
    conn.execute(
        """
        UPDATE native_notification_requests SET state = 'acknowledged', acknowledged_at = ?, last_action = ?, detail = ?, updated_at = ?
        WHERE identifier = ?
        """,
        (now_iso, action, f"{action} chosen on the macOS notification", now_iso, identifier),
    )
    if row["notification_id"]:
        effects.append(("action", row["notification_id"], action, identifier))


def _apply_effects(effects: list[tuple[Any, ...]], *, now_fn: Callable[[], str],
                   get_connection_fn: Callable[[], Any]) -> tuple[int, list[dict[str, str]]]:
    """Effects on bell items run after the request transaction commits, each through the notification centre."""
    from core.operator import notification_center

    applied, opened = 0, []
    for effect in effects:
        if effect[0] == "delivery":
            _kind, notification_id, delivery_state, detail, identifier = effect
            notification_center.record_delivery(notification_id, channel="macos", state=delivery_state, detail=detail,
                                                external_id=identifier, now_fn=now_fn, get_connection_fn=get_connection_fn)
            continue
        _kind, notification_id, action, identifier = effect
        result = notification_center.apply_action(notification_id, action=action, now_fn=now_fn, get_connection_fn=get_connection_fn)
        if result.get("ok"):
            applied += 1
            if action == "open":
                item = notification_center.load_item(notification_id, get_connection_fn=get_connection_fn) or {}
                opened.append({"notification_id": notification_id, "session_id": str(item.get("session_id") or ""),
                               "event_key": str(item.get("event_key") or ""), "source_kind": str(item.get("source_kind") or "")})
        notification_center.record_delivery(notification_id, channel="macos", state="acknowledged",
                                            detail=f"{action} chosen on the macOS notification", external_id=identifier,
                                            now_fn=now_fn, get_connection_fn=get_connection_fn)
    return applied, opened


def bridge_status(*, now_fn: Callable[[], str] = _utcnow, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """What Settings shows: whether the channel is on, the bridge's last contact, macOS's permission and recent requests."""
    from core.operator import notification_center

    preferences = notification_center.load_preferences(get_connection_fn=get_connection_fn)
    now = _parse(now_fn())
    conn = get_connection_fn()
    try:
        state = _load_state(conn)
        rows = conn.execute(
            """
            SELECT identifier, kind, notification_id, schedule_id, deliver_at_utc, state, detail, hand_count, submitted_at, listed_at,
                   acknowledged_at, updated_at
            FROM native_notification_requests ORDER BY updated_at DESC, created_at DESC LIMIT ?
            """,
            (STATUS_ROWS,),
        ).fetchall()
    finally:
        conn.close()
    seen = _parse(state["seen_at"])
    enabled = bool(preferences.get("native_notifications"))
    authorization = str(state["settings"].get("authorization") or "")
    connected = bool(seen is not None and now is not None and abs((now - seen).total_seconds()) <= CONNECTED_WITHIN_SECONDS)
    return {
        "ok": True,
        "enabled": bool(preferences.get("native_notifications")),
        "connected": connected,
        "seen_at": state["seen_at"],
        "helper_version": state["helper_version"],
        "authorization": str(state["settings"].get("authorization") or "") or "unknown",
        "settings": state["settings"],
        "want_authorization": bool(state["want_authorization"]),
        "authorization_request": state["authorization_request"],
        "recovery": _recovery(enabled=enabled, authorization=authorization, state=state, now=now),
        "states": list(REQUEST_STATES),
        "explanation": EXPLANATION,
        "requests": [dict(row) for row in rows],
    }


def request_authorization(*, now_fn: Callable[[], str] = _utcnow, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """Ask the bridge to have macOS ask the person (macOS shows its own dialog once; after that only System Settings)."""
    now_iso = now_fn()
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = _load_state(conn)
        state["want_authorization"] = True
        _save_state(conn, state, now_iso)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "authorization": str(state["settings"].get("authorization") or "") or "unknown"}


def send_test_notification(*, now_fn: Callable[[], str] = _utcnow, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """A bell item marked as a test, sent to macOS on the next bridge contact regardless of quiet hours."""
    from core.operator import notification_center

    preferences = notification_center.load_preferences(get_connection_fn=get_connection_fn)
    if not preferences.get("native_notifications"):
        return {"ok": False, "reason": "native_notifications_off", "detail": "turn on macOS notifications first", "status": 409}
    item = notification_center.record_item(dedupe_key=f"test:{uuid.uuid4().hex}", source_kind="test", title=TEST_TITLE, body=TEST_BODY,
                                           payload={"test": True}, now_fn=now_fn, get_connection_fn=get_connection_fn)
    notification_center.record_delivery(item["notification_id"], channel="in_app", state="recorded", now_fn=now_fn,
                                        get_connection_fn=get_connection_fn)
    return {"ok": True, "notification_id": item["notification_id"], "status": 200}


def preferences_changed(before: dict[str, Any], after: dict[str, Any], *, now_fn: Callable[[], str] = _utcnow,
                        get_connection_fn: Callable[[], Any] = _default_connection) -> None:
    """Turning the channel on starts it from now (older bell items are not sent) and asks for permission if macOS has
    not granted it; turning it off lets the next bridge contact withdraw what macOS still holds."""
    was_on, is_on = bool(before.get("native_notifications")), bool(after.get("native_notifications"))
    if was_on == is_on:
        return
    now_iso = now_fn()
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = _load_state(conn)
        if is_on:
            state["enabled_at"] = now_iso
            state["want_authorization"] = str(state["settings"].get("authorization") or "") not in SENDABLE
        else:
            state["enabled_at"], state["want_authorization"] = "", False
        _save_state(conn, state, now_iso)
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "EXPLANATION", "IDENTIFIER_PREFIX", "REQUEST_STATES", "SCHEDULE_AHEAD_HOURS", "bridge_status", "ingest_report", "outbox",
    "preferences_changed", "request_authorization", "send_test_notification",
]
