"""Today in VOOL: what is coming up and what is still open, read fresh from the stores that own it.

- ``reminders``  pending reminders from the reminder store (core.operator.reminders), overdue first,
                 then the ones due before the end of tomorrow on this computer's clock;
- ``calendar``   today's events from the last calendar sync (core.operator.calendar_alerts); when
                 the sync is older than its refresh window, the events are not shown as current;
- ``todos``      open items on the to-do list (core.pa_tasks);
- ``jobs``       goals that are not finished (core.goal_workflow), each first reconciled, so a goal
                 whose worker died with an earlier VOOL reads as interrupted, never as running.

Nothing is cached and nothing is made up: a store that cannot be read says so (``unreadable``), and
an empty one is empty. Every read is the same store the chat tools write, so a reminder set in a chat
is here on the next read, and a restart changes nothing but the clock.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

_MAX_ROWS = 8


def _local_zone():
    try:
        from core.operator.calendar_alerts import _user_zone_name, _zone

        return _zone(_user_zone_name())
    except Exception:
        return datetime.now().astimezone().tzinfo


def _parse(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _source(read) -> dict[str, Any]:
    try:
        return {"state": "ok", **read()}
    except Exception as exc:  # a store that cannot be read is never shown as empty
        return {"state": "unreadable", "fault": type(exc).__name__, "items": []}


def _reminders(now: datetime, horizon: datetime) -> dict[str, Any]:
    from core.operator.reminders import list_owner_reminders
    from storage.db import get_connection

    items: list[dict[str, Any]] = []
    later = 0
    for row in list_owner_reminders(get_connection_fn=get_connection):
        due = _parse(row.get("due_at_utc"))
        if due is None:
            continue
        if due > horizon:
            later += 1
            continue
        items.append({"id": str(row.get("reminder_id") or ""), "note": str(row.get("note") or ""),
                      "due_utc": due.astimezone(timezone.utc).isoformat(), "overdue": due < now,
                      "uncertain": str(row.get("status") or "") == "delivery_uncertain",
                      "session_id": str(row.get("session_id") or "")})
    return {"items": items[:_MAX_ROWS], "more": max(0, len(items) - _MAX_ROWS), "later": later}


def _calendar(now: datetime, zone) -> dict[str, Any]:
    from core.operator import calendar_accounts as accounts
    from core.operator import calendar_alerts as alerts

    now_iso = now.astimezone(timezone.utc).isoformat()
    synced = [accounts.present_account(a, now_iso=now_iso, stale_after_minutes=alerts.STALE_AFTER_MINUTES)
              for a in accounts.list_accounts() if a.get("sync_enabled")]
    if not synced:
        return {"connected": False, "items": []}
    stale = [a for a in synced if a["status"] != "connected"]
    if stale and len(stale) == len(synced):
        # Not one calendar has synced within its window: what the last sync read may have moved.
        last = max((str(a.get("last_sync_ok_at") or "") for a in synced), default="")
        return {"connected": True, "stale": True, "last_sync_utc": last, "items": []}
    today = now.astimezone(zone).date()
    items = []
    for event in alerts.upcoming_events(now_fn=lambda: now_iso):
        if event.get("all_day"):
            start = str(event.get("start_date") or "")
            if start and start > today.isoformat():
                continue
        else:
            start_local = _parse(event.get("start_local"))
            if start_local is None or start_local.astimezone(zone).date() != today:
                continue
        items.append({"title": str(event.get("title") or ""), "all_day": bool(event.get("all_day")),
                      "start_local": str(event.get("start_local") or ""), "end_local": str(event.get("end_local") or ""),
                      "calendar": str(event.get("calendar_name") or ""), "location": str(event.get("location") or "")})
    return {"connected": True, "stale": False, "some_stale": bool(stale), "items": items[:_MAX_ROWS],
            "more": max(0, len(items) - _MAX_ROWS)}


def _todos() -> dict[str, Any]:
    from core import pa_tasks

    rows = [t for t in pa_tasks.list_tasks(include_done=False) if t.get("status") == pa_tasks.STATUS_OPEN]
    return {"items": [{"id": t["task_id"], "title": t["title"], "due_utc": t.get("due_at_utc") or ""} for t in rows[:_MAX_ROWS]],
            "more": max(0, len(rows) - _MAX_ROWS)}


def _jobs() -> dict[str, Any]:
    from core import goal_workflow as goals

    conn = goals._conn()
    goals._ensure_table(conn)
    terminal = tuple(sorted(goals.TERMINAL_GOAL_STATES))
    rows = conn.execute(
        f"SELECT goal_id FROM chat_goals WHERE state NOT IN ({','.join('?' * len(terminal))}) ORDER BY created_at DESC LIMIT 20",
        terminal,
    ).fetchall()
    items = []
    for row in rows:
        goal_id = str(row[0])
        goals.reconcile_goal_if_orphaned(goal_id)  # a worker that died with an earlier VOOL is not "running"
        goal = goals.load_goal(goal_id)
        if goal is None or str(goal.get("state")) in goals.TERMINAL_GOAL_STATES:
            continue
        done, total = goals.completed_step_count(goal.get("plan") if isinstance(goal.get("plan"), dict) else {})
        items.append({"id": goal_id, "objective": str(goal.get("objective") or ""), "state": str(goal.get("state") or ""),
                      "done": done, "total": total, "session_id": str(goal.get("session_id") or "")})
    return {"items": items[:_MAX_ROWS], "more": max(0, len(items) - _MAX_ROWS)}


def today(*, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    zone = _local_zone()
    local = moment.astimezone(zone)
    horizon = (local + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "ok": True,
        "read_at": moment.astimezone(timezone.utc).isoformat(),
        "reminders": _source(lambda: _reminders(moment, horizon)),
        "calendar": _source(lambda: _calendar(moment, zone)),
        "todos": _source(_todos),
        "jobs": _source(_jobs),
    }


__all__ = ["today"]
