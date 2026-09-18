"""Upcoming-event alerts: events on opted-in calendars become entries on the one alert schedule.

The provider holds the events. VOOL keeps projections of them (the last copy it read) and owns only what it
adds: the alert schedule. The flow runs through existing authorities:

* The sync reads each opted-in account's chosen calendars for a bounded window (the last hour and the next 36
  hours) inside the named background scope ``calendar.alert_sync``, so the provider call crosses the one outbound
  door and the KAS transport like any other effect.
* Each event becomes a projection keyed by account, calendar, provider id and occurrence. Its alerts become rows
  on ``reminder_requests`` with source kind ``calendar_alert``, one per lead time, keyed by the event, the lead
  and the start instant. An unchanged event never schedules twice; a moved event supersedes the alerts for its
  old time.
* An event no longer on a fully read calendar has its pending alerts cancelled. A read that failed (a refusal, an
  unreadable reply, the network) keeps every pending alert and reports the account's state instead.
* The due dispatcher delivers alerts into the notification centre. Alerts that come due after their event
  already started are collected into one catch-up notice rather than shown one by one.

Sync runs on a bounded cadence per account (every 10 minutes, backing off after failures), never on chat turns,
and only while a VOOL process is running. Nothing here watches a calendar while VOOL is closed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SYNC_SCOPE_NAME = "calendar.alert_sync"
HORIZON_HOURS = 36
LOOKBACK_MINUTES = 60
REFRESH_MINUTES = 10
MAX_BACKOFF_MINUTES = 60
STALE_AFTER_MINUTES = 3 * REFRESH_MINUTES
MAX_EVENTS_PER_CALENDAR = 500
MAX_LEAD_TIMES = 5
MAX_LEAD_MINUTES = 7 * 24 * 60
DEFAULT_LEAD_MINUTES = [15]
WORKER_TICK_SECONDS = 30.0
WORKER_ACCOUNTS_PER_TICK = 10
DESCRIPTION_EXCERPT_CHARS = 280
_SQL_CHUNK = 400

#: What the alert schedule covers and what it does not, shown beside the alert settings.
COVERAGE_NOTE = (
    "While VOOL is running it refreshes each chosen calendar every 10 minutes and schedules alerts for the next "
    "36 hours. While VOOL is closed it does not fetch calendars; alerts that passed in the meantime are collected "
    "into one notice when it starts again."
)

_UPSERT_PROJECTION = """
INSERT INTO calendar_event_projections (
    event_key, account_id, calendar_id, uid, occurrence_key, series_id, summary, start_utc, end_utc, all_day, start_date, end_date,
    tz_name, location, meeting_url, web_url, description_excerpt, etag, state, first_seen_at, last_seen_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(event_key) DO UPDATE SET
    series_id = excluded.series_id, summary = excluded.summary, start_utc = excluded.start_utc, end_utc = excluded.end_utc, all_day = excluded.all_day,
    start_date = excluded.start_date, end_date = excluded.end_date, tz_name = excluded.tz_name,
    location = excluded.location, meeting_url = excluded.meeting_url, web_url = excluded.web_url,
    description_excerpt = excluded.description_excerpt, etag = excluded.etag, state = excluded.state,
    last_seen_at = excluded.last_seen_at, updated_at = excluded.updated_at
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


def _iso_utc(value: Any) -> str:
    parsed = _parse(value)
    return parsed.astimezone(timezone.utc).isoformat() if parsed is not None else ""


def _user_zone_name() -> str:
    try:
        from core.user_preferences import load_user_timezone

        name = str(load_user_timezone() or "").strip()
    except Exception:
        name = ""
    try:
        ZoneInfo(name or "UTC")
    except Exception:
        return "UTC"
    return name or "UTC"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def https_link(value: Any) -> str:
    """An https address with a host, or "" -- the only links a card or a notification may show."""
    text = str(value or "").strip()
    if not text or len(text) > 2048 or re.search(r"[\s\"'<>]", text):
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    return text if parts.scheme == "https" and parts.netloc else ""


def _excerpt(value: Any) -> str:
    return " ".join(str(value or "").split())[:DESCRIPTION_EXCERPT_CHARS]


def event_key_for(account_id: str, calendar_id: str, uid: str, occurrence: str = "") -> str:
    """The one identity of an event occurrence on one calendar of one account."""
    digest = hashlib.sha256("\0".join([str(account_id), str(calendar_id), str(uid), str(occurrence)]).encode("utf-8")).hexdigest()
    return "evt-" + digest[:40]


def occurrence_key(event: Any) -> str:
    """The occurrence part of an event's identity: the provider's original start of one occurrence of a series, as
    UTC, or "" for an event that is not an occurrence.

    A moved occurrence keeps its original start, so its identity (and any per-event alert policy) survives the move.
    Providers that give each occurrence its own id (Google instances, Graph occurrences) stay distinct either way;
    EventKit serves every occurrence under the series' identifier, where this part is what tells them apart.
    """
    original = str(getattr(event, "original_start", "") or "").strip()
    if not original:
        return ""
    return _iso_utc(original) or original


def _schedule_key(event_key: str, lead_minutes: int, start_utc: str) -> str:
    return "alert-" + hashlib.sha256(f"{event_key}\0{int(lead_minutes)}\0{start_utc}".encode()).hexdigest()[:40]


def normalize_lead_minutes(minutes: Any) -> list[int] | None:
    """``None`` inherits, ``[]`` turns alerts off, otherwise up to five distinct whole minutes, largest first."""
    if minutes is None:
        return None
    if not isinstance(minutes, (list, tuple)):
        raise ValueError("lead minutes must be a list of whole minutes")
    values = []
    for value in minutes:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("lead minutes must be whole numbers")
        if not 0 <= value <= MAX_LEAD_MINUTES:
            raise ValueError(f"a lead time must be between 0 and {MAX_LEAD_MINUTES} minutes")
        values.append(int(value))
    unique = sorted(set(values), reverse=True)
    if len(unique) > MAX_LEAD_TIMES:
        raise ValueError(f"at most {MAX_LEAD_TIMES} lead times per event")
    return unique


def effective_lead_minutes(*, account: dict[str, Any], calendar_leads: list[int] | None, event_leads: list[int] | None) -> list[int]:
    if event_leads is not None:
        return list(event_leads)
    if calendar_leads is not None:
        return list(calendar_leads)
    return list(account.get("default_lead_minutes") or [])


def _lead_source(*, calendar_leads: list[int] | None, event_leads: list[int] | None) -> str:
    return "event" if event_leads is not None else "calendar" if calendar_leads is not None else "account"


def set_lead_minutes(
    *,
    account_id: str = "",
    calendar_id: str = "",
    event_key: str = "",
    minutes: Any,
    apply_now: bool = False,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Set the alert lead times for one event, one calendar or an account's default.

    The change is applied by the next sync of the account, or at once from the stored projections when
    ``apply_now`` is set (no provider call).
    """
    try:
        normalized = normalize_lead_minutes(minutes)
    except ValueError as exc:
        return {"ok": False, "reason": "invalid_lead_minutes", "detail": str(exc)}
    encoded = None if normalized is None else json.dumps(normalized)
    now = now_fn()
    conn = get_connection_fn()
    try:
        if event_key:
            scope = "event"
            cursor = conn.execute(
                "UPDATE calendar_event_projections SET alert_lead_minutes_json = ?, updated_at = ? WHERE event_key = ?",
                (encoded, now, event_key),
            )
        elif account_id and calendar_id:
            scope = "calendar"
            cursor = conn.execute(
                "UPDATE calendar_selections SET alert_lead_minutes_json = ?, updated_at = ? WHERE account_id = ? AND calendar_id = ?",
                (encoded, now, account_id, calendar_id),
            )
        elif account_id:
            scope = "account"
            cursor = conn.execute(
                "UPDATE calendar_accounts SET default_lead_minutes_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(DEFAULT_LEAD_MINUTES if normalized is None else normalized), now, account_id),
            )
        else:
            return {"ok": False, "reason": "no_target", "detail": "name an event, a calendar or an account"}
        changed = int(cursor.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    if changed == 0:
        return {"ok": False, "reason": "unknown_target", "detail": "no such event, calendar or account"}
    result: dict[str, Any] = {
        "ok": True, "scope": scope, "lead_minutes": DEFAULT_LEAD_MINUTES if scope == "account" and normalized is None else normalized,
        "inherits": normalized is None and scope != "account",
    }
    if apply_now:
        result["applied"] = reschedule_from_projections(
            account_id=account_id, calendar_id=calendar_id, event_key=event_key, now_fn=now_fn, get_connection_fn=get_connection_fn,
        )
    return result


def _projection(row: Any) -> dict[str, Any]:
    from core.operator.calendar_accounts import decode_minutes

    data = dict(row)
    data["all_day"] = bool(data.get("all_day"))
    data["alert_lead_minutes"] = decode_minutes(data.pop("alert_lead_minutes_json", None), default=None)
    return data


def _alert_payload(*, account: dict[str, Any], calendar_name: str, projection: dict[str, Any], lead_minutes: int, zone_name: str) -> dict[str, Any]:
    zone = _zone(zone_name)
    start, end = _parse(projection.get("start_utc")), _parse(projection.get("end_utc"))
    return {
        "event_key": projection["event_key"],
        "account_id": account["account_id"],
        "account_label": str(account.get("label") or ""),
        "provider": str(account.get("provider") or ""),
        "calendar_id": projection["calendar_id"],
        "calendar_name": str(calendar_name or ""),
        "title": str(projection.get("summary") or "") or "(untitled event)",
        "start_utc": start.astimezone(timezone.utc).isoformat() if start else "",
        "end_utc": end.astimezone(timezone.utc).isoformat() if end else "",
        "start_local": start.astimezone(zone).isoformat() if start else "",
        "end_local": end.astimezone(zone).isoformat() if end else "",
        "tz_name": zone_name,
        "location": str(projection.get("location") or ""),
        "meeting_url": https_link(projection.get("meeting_url")),
        "web_url": https_link(projection.get("web_url")),
        "lead_minutes": int(lead_minutes),
    }


def _desired_alerts(
    *,
    account: dict[str, Any],
    calendar_name: str,
    calendar_leads: list[int] | None,
    projection: dict[str, Any],
    now: datetime,
    late_grace_minutes: int,
    zone_name: str,
) -> list[dict[str, Any]] | None:
    """The alerts an event should have, or ``None`` when it already started: its existing alerts then stay as
    they are, for the dispatcher's catch-up rules."""
    if projection.get("state") != "active" or projection.get("all_day") or not projection.get("start_utc"):
        return []
    start = _parse(projection["start_utc"])
    if start is None:
        return []
    if start <= now - timedelta(minutes=late_grace_minutes):
        return None
    if not account.get("alerts_enabled"):
        return []
    leads = effective_lead_minutes(account=account, calendar_leads=calendar_leads, event_leads=projection.get("alert_lead_minutes"))
    start_iso = start.astimezone(timezone.utc).isoformat()
    return [
        {
            "schedule_key": _schedule_key(projection["event_key"], lead, start_iso),
            "due_at_utc": (start - timedelta(minutes=lead)).astimezone(timezone.utc).isoformat(),
            "tz_name": zone_name,
            "payload": _alert_payload(account=account, calendar_name=calendar_name, projection=projection, lead_minutes=lead, zone_name=zone_name),
        }
        for lead in leads
    ]


def _apply_desired(event_key: str, desired: list[dict[str, Any]], *, title: str, now_fn: Callable[[], str],
                   get_connection_fn: Callable[[], Any]) -> dict[str, Any]:
    from core.operator import notification_center, reminders

    counts = reminders.reconcile_event_alerts(
        event_key=event_key, desired=desired, note=title, now_fn=now_fn, get_connection_fn=get_connection_fn,
    )
    if counts["superseded_ids"]:
        notification_center.supersede_items_for_schedules(counts["superseded_ids"], now_fn=now_fn, get_connection_fn=get_connection_fn)
    return counts


_ACCOUNT_LOCKS: dict[str, threading.Lock] = {}
_ACCOUNT_LOCKS_GUARD = threading.Lock()


def _account_lock(account_id: str) -> threading.Lock:
    with _ACCOUNT_LOCKS_GUARD:
        return _ACCOUNT_LOCKS.setdefault(str(account_id), threading.Lock())


def next_sync_due(now: datetime, *, ok: bool, failures_before: int) -> str:
    """Ten minutes after a good sync; after a failure, 10, 20, 40, then 60 minutes."""
    if ok:
        return (now + timedelta(minutes=REFRESH_MINUTES)).isoformat()
    minutes = min(MAX_BACKOFF_MINUTES, REFRESH_MINUTES * (2 ** max(0, int(failures_before))))
    return (now + timedelta(minutes=minutes)).isoformat()


def sync_account(
    account_id: str,
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
    adapter_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Read one opted-in account's chosen calendars for the window and reconcile their alerts."""
    from core.operator import calendar_accounts as accounts
    from core.operator import notification_center

    started = now_fn()
    now = _parse(started) or datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "ok": False, "account_id": str(account_id), "status": "", "detail": "", "events_seen": 0, "alerts_scheduled": 0,
        "alerts_superseded": 0, "alerts_cancelled": 0, "events_already_started": 0, "calendars": [], "synced_at": started,
    }
    account = accounts.load_account(account_id, get_connection_fn=get_connection_fn)
    if account is None:
        report["status"] = "unknown_account"
        return report
    if account["status"] == "disconnected":
        report.update(status="disconnected", detail=accounts.RECOVERY["disconnected"])
        return report
    if not account["sync_enabled"]:
        report.update(status="sync_off", detail="sync is off for this account")
        return report
    lock = _account_lock(account_id)
    if not lock.acquire(blocking=False):
        report.update(status="sync_in_progress", detail="another sync of this account is running")
        return report
    try:
        selections = [row for row in accounts.list_selections(account_id, get_connection_fn=get_connection_fn) if row["selected"] and row["present"]]
        window_start = now - timedelta(minutes=LOOKBACK_MINUTES)
        window_end = now + timedelta(hours=HORIZON_HOURS)
        report["window"] = {"start_utc": window_start.isoformat(), "end_utc": window_end.isoformat()}
        preferences = notification_center.load_preferences(get_connection_fn=get_connection_fn)
        late_grace = int(preferences.get("late_grace_minutes") or 0)
        zone_name = _user_zone_name()
        failures: list[tuple[str, str]] = []
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope(SYNC_SCOPE_NAME):
            adapter = None
            try:
                adapter = (adapter_factory or accounts.build_adapter)(account)
            except Exception as exc:
                failures.append(accounts.classify_calendar_error(exc))
            for selection in selections if adapter is not None else []:
                calendar_report: dict[str, Any] = {
                    "calendar_id": selection["calendar_id"], "display_name": selection["display_name"], "ok": False,
                    "events": 0, "scan_limit_hit": False,
                }
                report["calendars"].append(calendar_report)
                try:
                    events = list(adapter.events_in_range(
                        selection["calendar_id"], start_utc=window_start.isoformat(), end_utc=window_end.isoformat(),
                    ))
                except Exception as exc:
                    status, detail = accounts.classify_calendar_error(exc)
                    failures.append((status, detail))
                    calendar_report.update(status=status, detail=detail)
                    continue
                _process_calendar(
                    account=account, selection=selection, events=events, window_start=window_start, window_end=window_end,
                    now=now, late_grace=late_grace, zone_name=zone_name, report=report, calendar_report=calendar_report,
                    now_fn=now_fn, get_connection_fn=get_connection_fn,
                )
        ok = not failures
        status, detail = ("connected", "") if ok else failures[0]
        accounts.record_sync_result(
            account_id, ok=ok, status=status, detail=detail, started_at=started,
            next_due_at=next_sync_due(now, ok=ok, failures_before=int(account.get("sync_failures") or 0)),
            get_connection_fn=get_connection_fn,
        )
        report.update(ok=ok, status=status, detail=detail)
        if ok and not selections:
            report["detail"] = "no calendars are chosen for this account"
        return report
    finally:
        lock.release()


def _process_calendar(
    *,
    account: dict[str, Any],
    selection: dict[str, Any],
    events: list[Any],
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    late_grace: int,
    zone_name: str,
    report: dict[str, Any],
    calendar_report: dict[str, Any],
    now_fn: Callable[[], str],
    get_connection_fn: Callable[[], Any],
) -> None:
    from core.operator import notification_center, reminders

    account_id, calendar_id = account["account_id"], selection["calendar_id"]
    limited = events[:MAX_EVENTS_PER_CALENDAR]
    calendar_report["scan_limit_hit"] = len(events) > MAX_EVENTS_PER_CALENDAR
    stamp = now_fn()
    seen: set[str] = set()
    for event in limited:
        key = event_key_for(account_id, calendar_id, str(event.uid), occurrence_key(event))
        if key in seen:
            continue
        seen.add(key)
        projection = _upsert_projection(
            account_id=account_id, calendar_id=calendar_id, event=event, event_key=key, stamp=stamp, get_connection_fn=get_connection_fn,
        )
        report["events_seen"] += 1
        desired = _desired_alerts(
            account=account, calendar_name=selection["display_name"], calendar_leads=selection["alert_lead_minutes"],
            projection=projection, now=now, late_grace_minutes=late_grace, zone_name=zone_name,
        )
        if desired is None:
            report["events_already_started"] += 1
            continue
        counts = _apply_desired(key, desired, title=str(projection.get("summary") or ""), now_fn=now_fn, get_connection_fn=get_connection_fn)
        report["alerts_scheduled"] += counts["scheduled"]
        report["alerts_superseded"] += counts["superseded"]
    vanished: set[str] = set()
    if not calendar_report["scan_limit_hit"]:
        # Only a complete read of the window proves an event is gone; a truncated one proves nothing about the rest.
        vanished = _keys_in_window(account_id, calendar_id, window_start, window_end, get_connection_fn=get_connection_fn) - seen
    for key in sorted(vanished):
        conn = get_connection_fn()
        try:
            conn.execute(
                "UPDATE calendar_event_projections SET state = 'missing', updated_at = ? WHERE event_key = ?",
                (stamp, key),
            )
            conn.commit()
        finally:
            conn.close()
        cancelled = reminders.cancel_calendar_alert_rows(
            event_keys=[key], reason="the event is no longer on the calendar", now_fn=now_fn, get_connection_fn=get_connection_fn,
        )
        if cancelled:
            notification_center.supersede_items_for_schedules(cancelled, now_fn=now_fn, get_connection_fn=get_connection_fn)
        report["alerts_cancelled"] += len(cancelled)
    calendar_report.update(ok=True, events=len(limited), vanished=len(vanished))


def _upsert_projection(*, account_id: str, calendar_id: str, event: Any, event_key: str, stamp: str,
                       get_connection_fn: Callable[[], Any]) -> dict[str, Any]:
    all_day = bool(getattr(event, "all_day", False))
    state = "cancelled" if bool(getattr(event, "cancelled", False)) else "active"
    values = (
        event_key, account_id, calendar_id, str(event.uid), occurrence_key(event), str(getattr(event, "series_id", "") or "")[:300],
        str(getattr(event, "summary", "") or "")[:300],
        "" if all_day else _iso_utc(getattr(event, "start_utc", "")),
        "" if all_day else _iso_utc(getattr(event, "end_utc", "")),
        1 if all_day else 0,
        str(getattr(event, "start_date", "") or ""), str(getattr(event, "end_date", "") or ""),
        str(getattr(event, "tz_name", "") or ""),
        " ".join(str(getattr(event, "location", "") or "").split())[:300],
        https_link(getattr(event, "meeting_url", "")),
        https_link(getattr(event, "web_url", "")),
        _excerpt(getattr(event, "description", "")),
        str(getattr(event, "etag", "") or ""),
        state, stamp, stamp, stamp,
    )
    conn = get_connection_fn()
    try:
        conn.execute(_UPSERT_PROJECTION, values)
        conn.commit()
        row = conn.execute("SELECT * FROM calendar_event_projections WHERE event_key = ?", (event_key,)).fetchone()
    finally:
        conn.close()
    return _projection(row)


def _keys_in_window(account_id: str, calendar_id: str, window_start: datetime, window_end: datetime, *,
                    get_connection_fn: Callable[[], Any]) -> set[str]:
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            """
            SELECT event_key FROM calendar_event_projections
            WHERE account_id = ? AND calendar_id = ? AND state IN ('active', 'cancelled')
              AND ((all_day = 0 AND start_utc >= ? AND start_utc < ?) OR (all_day = 1 AND start_date >= ? AND start_date < ?))
            """,
            (account_id, calendar_id, window_start.astimezone(timezone.utc).isoformat(), window_end.astimezone(timezone.utc).isoformat(),
             window_start.date().isoformat(), window_end.date().isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return {str(row["event_key"]) for row in rows}


def reschedule_from_projections(
    *,
    account_id: str = "",
    calendar_id: str = "",
    event_key: str = "",
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """Recompute alerts from the stored projections after a policy change, without a provider call."""
    from core.operator import calendar_accounts as accounts
    from core.operator import notification_center

    clauses, params = ["p.state = 'active'", "s.selected = 1"], []
    if event_key:
        clauses.append("p.event_key = ?")
        params.append(event_key)
    if account_id:
        clauses.append("p.account_id = ?")
        params.append(account_id)
    if calendar_id:
        clauses.append("p.calendar_id = ?")
        params.append(calendar_id)
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            f"""
            SELECT p.*, s.display_name AS calendar_name, s.alert_lead_minutes_json AS calendar_leads_json
            FROM calendar_event_projections p
            JOIN calendar_selections s ON s.account_id = p.account_id AND s.calendar_id = p.calendar_id
            WHERE {' AND '.join(clauses)}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    now = _parse(now_fn()) or datetime.now(timezone.utc)
    late_grace = int(notification_center.load_preferences(get_connection_fn=get_connection_fn).get("late_grace_minutes") or 0)
    zone_name = _user_zone_name()
    cache: dict[str, dict[str, Any] | None] = {}
    totals = {"alerts_scheduled": 0, "alerts_superseded": 0}
    for row in rows:
        data = dict(row)
        calendar_name = str(data.pop("calendar_name", "") or "")
        calendar_leads = accounts.decode_minutes(data.pop("calendar_leads_json", None), default=None)
        projection = _projection(data)
        owner = projection["account_id"]
        if owner not in cache:
            cache[owner] = accounts.load_account(owner, get_connection_fn=get_connection_fn)
        account = cache[owner]
        if account is None or account["status"] == "disconnected":
            continue
        desired = _desired_alerts(account=account, calendar_name=calendar_name, calendar_leads=calendar_leads, projection=projection,
                                  now=now, late_grace_minutes=late_grace, zone_name=zone_name)
        if desired is None:
            continue
        counts = _apply_desired(projection["event_key"], desired, title=str(projection.get("summary") or ""), now_fn=now_fn,
                                get_connection_fn=get_connection_fn)
        totals["alerts_scheduled"] += counts["scheduled"]
        totals["alerts_superseded"] += counts["superseded"]
    return totals


def cancel_calendar_alerts(
    *,
    account_id: str,
    calendar_id: str = "",
    reason: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> int:
    """Cancel the pending alerts of one account (or one of its calendars). Shown alerts are history and stay."""
    from core.operator import notification_center, reminders

    conn = get_connection_fn()
    try:
        if calendar_id:
            rows = conn.execute(
                "SELECT event_key FROM calendar_event_projections WHERE account_id = ? AND calendar_id = ?", (account_id, calendar_id),
            ).fetchall()
        else:
            rows = conn.execute("SELECT event_key FROM calendar_event_projections WHERE account_id = ?", (account_id,)).fetchall()
    finally:
        conn.close()
    keys = [str(row["event_key"]) for row in rows]
    cancelled: list[str] = []
    for index in range(0, len(keys), _SQL_CHUNK):
        cancelled.extend(reminders.cancel_calendar_alert_rows(
            event_keys=keys[index:index + _SQL_CHUNK], reason=reason, now_fn=now_fn, get_connection_fn=get_connection_fn,
        ))
    if cancelled:
        notification_center.supersede_items_for_schedules(cancelled, now_fn=now_fn, get_connection_fn=get_connection_fn)
    return len(cancelled)


def upcoming_alerts(
    *,
    now_fn: Callable[[], str] = _utcnow,
    limit: int = 50,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> list[dict[str, Any]]:
    """Pending calendar alerts, soonest first."""
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            """
            SELECT reminder_id, due_at_utc, payload_json, source_ref, snooze_count FROM reminder_requests
            WHERE source_kind = 'calendar_alert' AND status = 'scheduled'
            ORDER BY due_at_utc ASC, reminder_id ASC LIMIT ?
            """,
            (max(1, min(int(limit or 50), 500)),),
        ).fetchall()
    finally:
        conn.close()
    alerts = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        alerts.append({
            "reminder_id": row["reminder_id"], "event_key": row["source_ref"], "due_at_utc": row["due_at_utc"],
            "snoozed": int(row["snooze_count"] or 0) > 0, "title": payload.get("title") or "",
            "lead_minutes": payload.get("lead_minutes"), "start_utc": payload.get("start_utc") or "",
            "start_local": payload.get("start_local") or "", "calendar_name": payload.get("calendar_name") or "",
            "account_id": payload.get("account_id") or "", "account_label": payload.get("account_label") or "",
            "location": payload.get("location") or "", "meeting_url": https_link(payload.get("meeting_url")),
        })
    return alerts


def upcoming_events(
    *,
    now_fn: Callable[[], str] = _utcnow,
    limit: int = 100,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> list[dict[str, Any]]:
    """The events the last syncs read that have not ended, with their effective alert policy."""
    from core.operator import calendar_accounts as accounts

    now = _parse(now_fn()) or datetime.now(timezone.utc)
    zone_name = _user_zone_name()
    zone = _zone(zone_name)
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            """
            SELECT p.*, s.display_name AS calendar_name, s.alert_lead_minutes_json AS calendar_leads_json,
                   a.label AS account_label, a.provider AS provider, a.default_lead_minutes_json AS account_leads_json,
                   a.alerts_enabled AS account_alerts_enabled
            FROM calendar_event_projections p
            JOIN calendar_selections s ON s.account_id = p.account_id AND s.calendar_id = p.calendar_id
            JOIN calendar_accounts a ON a.account_id = p.account_id
            WHERE p.state = 'active' AND s.selected = 1 AND a.status != 'disconnected'
              AND ((p.all_day = 0 AND p.end_utc >= ?) OR (p.all_day = 1 AND p.end_date >= ?))
            ORDER BY CASE WHEN p.all_day = 1 THEN p.start_date ELSE p.start_utc END ASC
            LIMIT ?
            """,
            (now.astimezone(timezone.utc).isoformat(), now.astimezone(zone).date().isoformat(), max(1, min(int(limit or 100), 500))),
        ).fetchall()
    finally:
        conn.close()
    events = []
    for row in rows:
        data = dict(row)
        calendar_leads = accounts.decode_minutes(data.pop("calendar_leads_json", None), default=None)
        account_view = {"default_lead_minutes": accounts.decode_minutes(data.pop("account_leads_json", None), default=[15])}
        alerts_enabled = bool(data.pop("account_alerts_enabled", 0))
        projection = _projection(data)
        start, end = _parse(projection.get("start_utc")), _parse(projection.get("end_utc"))
        events.append({
            "event_key": projection["event_key"], "title": projection.get("summary") or "(untitled event)",
            "all_day": projection["all_day"], "start_date": projection.get("start_date") or "", "end_date": projection.get("end_date") or "",
            "start_local": start.astimezone(zone).isoformat() if start else "", "end_local": end.astimezone(zone).isoformat() if end else "",
            "calendar_name": data.get("calendar_name") or "", "account_label": data.get("account_label") or "",
            "provider": data.get("provider") or "", "location": projection.get("location") or "",
            "meeting_url": https_link(projection.get("meeting_url")), "web_url": https_link(projection.get("web_url")),
            "alerts_enabled": alerts_enabled,
            "lead_minutes": effective_lead_minutes(account=account_view, calendar_leads=calendar_leads, event_leads=projection["alert_lead_minutes"]),
            "lead_source": _lead_source(calendar_leads=calendar_leads, event_leads=projection["alert_lead_minutes"]),
        })
    return events


def alerts_view(*, now_fn: Callable[[], str] = _utcnow, get_connection_fn: Callable[[], Any] = _default_connection) -> dict[str, Any]:
    """Everything the alert settings and the bell's event cards show: accounts, calendars, alerts, events, policy."""
    from core.operator import calendar_accounts as accounts
    from core.operator import notification_center

    now_iso = now_fn()
    view_accounts = []
    for account in accounts.list_accounts(get_connection_fn=get_connection_fn):
        presented = accounts.present_account(account, now_iso=now_iso, stale_after_minutes=STALE_AFTER_MINUTES)
        presented["calendars"] = accounts.list_selections(account["account_id"], get_connection_fn=get_connection_fn)
        view_accounts.append(presented)
    return {
        "accounts": view_accounts,
        "upcoming": upcoming_alerts(now_fn=now_fn, get_connection_fn=get_connection_fn),
        "events": upcoming_events(now_fn=now_fn, get_connection_fn=get_connection_fn),
        "preferences": notification_center.load_preferences(get_connection_fn=get_connection_fn),
        "providers": accounts.PROVIDERS,
        "policy": {"default_lead_minutes": DEFAULT_LEAD_MINUTES, "horizon_hours": HORIZON_HOURS, "refresh_minutes": REFRESH_MINUTES,
                   "max_lead_times": MAX_LEAD_TIMES},
        "coverage": COVERAGE_NOTE,
        "user_timezone": _user_zone_name(),
    }


def sync_now(
    account_id: str = "",
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any] = _default_connection,
) -> dict[str, Any]:
    """An explicit refresh: one account, or every opted-in account (bounded)."""
    from core.operator import calendar_accounts as accounts

    if account_id:
        ids = [account_id]
    else:
        ids = [row["account_id"] for row in accounts.list_accounts(get_connection_fn=get_connection_fn)
               if row["sync_enabled"] and row["status"] != "disconnected"]
    reports = [sync_account(value, now_fn=now_fn, get_connection_fn=get_connection_fn) for value in ids[:WORKER_ACCOUNTS_PER_TICK]]
    return {"ok": all(report["ok"] for report in reports), "reports": reports}


class CalendarAlertSync:
    """The background worker: syncs accounts whose refresh is due, a few per tick."""

    def __init__(self, *, interval_seconds: float = WORKER_TICK_SECONDS, now_fn: Callable[[], str] | None = None,
                 get_connection_fn: Callable[[], Any] | None = None) -> None:
        self._interval = max(5.0, float(interval_seconds))
        self._now_fn = now_fn or _utcnow
        self._get_connection_fn = get_connection_fn or _default_connection
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def due_accounts(self) -> list[str]:
        conn = self._get_connection_fn()
        try:
            rows = conn.execute(
                """
                SELECT account_id FROM calendar_accounts
                WHERE sync_enabled = 1 AND status != 'disconnected' AND (next_sync_due_at = '' OR next_sync_due_at <= ?)
                ORDER BY next_sync_due_at ASC LIMIT ?
                """,
                (self._now_fn(), WORKER_ACCOUNTS_PER_TICK),
            ).fetchall()
        finally:
            conn.close()
        return [str(row["account_id"]) for row in rows]

    def run_due_once(self) -> list[dict[str, Any]]:
        reports = []
        for account_id in self.due_accounts():
            if self._stop.is_set():
                break
            try:
                reports.append(sync_account(account_id, now_fn=self._now_fn, get_connection_fn=self._get_connection_fn))
            except Exception:
                logger.exception("calendar alert sync failed for %s", account_id)
        return reports

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vool-calendar-alert-sync", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due_once()
            except Exception:
                logger.exception("calendar alert sync tick failed")
            self._stop.wait(self._interval)


_DEFAULT_SYNC: CalendarAlertSync | None = None
_DEFAULT_SYNC_LOCK = threading.Lock()


def start_default_sync() -> CalendarAlertSync:
    """Start the one process-wide calendar alert sync. Called by the served boot; safe to call twice."""
    global _DEFAULT_SYNC
    with _DEFAULT_SYNC_LOCK:
        if _DEFAULT_SYNC is not None and _DEFAULT_SYNC._thread is not None and _DEFAULT_SYNC._thread.is_alive():
            return _DEFAULT_SYNC
        _DEFAULT_SYNC = CalendarAlertSync()
        _DEFAULT_SYNC.start()
        return _DEFAULT_SYNC


def stop_default_sync() -> None:
    global _DEFAULT_SYNC
    with _DEFAULT_SYNC_LOCK:
        if _DEFAULT_SYNC is not None:
            _DEFAULT_SYNC.stop()
        _DEFAULT_SYNC = None


__all__ = [
    "COVERAGE_NOTE", "DEFAULT_LEAD_MINUTES", "HORIZON_HOURS", "REFRESH_MINUTES", "CalendarAlertSync", "alerts_view",
    "cancel_calendar_alerts", "effective_lead_minutes", "event_key_for", "https_link", "next_sync_due", "normalize_lead_minutes",
    "reschedule_from_projections", "set_lead_minutes", "start_default_sync", "stop_default_sync", "sync_account", "sync_now",
    "upcoming_alerts", "upcoming_events",
]
