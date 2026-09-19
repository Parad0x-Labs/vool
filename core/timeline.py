"""Deterministic timeline queries over existing durable event/activity stores.

Provides ``events_between``, ``history_on``, and ``latest`` by querying the
existing SQLite spine (``runtime_session_events``, ``execution_facts``,
``runtime_sessions``) — never an in-memory cache or second event ledger.

Every query:
    - survives restart (durable SQLite)
    - orders deterministically (created_at ASC / seq ASC)
    - uses canonical UTC storage/query boundaries
    - never fabricates missing events
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from core.time_range_resolver import TimeRange
from core.time_range_resolver import resolve as _resolve_range
from storage.db import get_connection

_log = logging.getLogger(__name__)


# ── Query result shapes ─────────────────────────────────────────────────────


@dataclass
class TimelineEvent:
    """One event from a timeline query.

    Every field is a direct projection from the source table — no fabrication,
    no augmentation.
    """

    source: str  # which table ("runtime_session_events", "execution_facts", etc.)
    event_id: str = ""  # session_id+seq for events, fact_id for execution_facts
    session_id: str = ""
    event_type: str = ""
    kind: str = ""  # for execution_facts: kind column
    name: str = ""  # for execution_facts: name column
    status: str = ""
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
            "created_at": self.created_at,
        }


@dataclass
class TimelineResult:
    """Result of a timeline query."""

    events: list[TimelineEvent]
    range_start: str  # ISO UTC
    range_end: str  # ISO UTC
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [e.to_dict() for e in self.events],
            "range_start": self.range_start,
            "range_end": self.range_end,
            "total": self.total,
        }


# ── Query implementation ────────────────────────────────────────────────────


def _parse_iso(iso_str: str) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp string to an aware datetime.

    Accepts formats like ``2026-08-25T14:30:00.123456`` (with or without Z
    suffix). Returns ``None`` on failure.
    """
    if not iso_str or not isinstance(iso_str, str):
        return None
    cleaned = iso_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _query_session_events(
    start_dt: datetime, end_dt: datetime
) -> list[TimelineEvent]:
    """Query `runtime_session_events` for events within [start_dt, end_dt)."""
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()
    events: list[TimelineEvent] = []
    conn = get_connection()
    try:
        # Table may not exist (e.g. fresh DB, test env)
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_session_events'"
        ).fetchone()
        if not table_check:
            return []
        rows = conn.execute(
            """
            SELECT session_id, seq, event_type, message, details_json, created_at
            FROM runtime_session_events
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at ASC, seq ASC
            """,
            (start_iso, end_iso),
        ).fetchall()
        for row in rows:
            detail = {}
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                detail = json.loads(row["details_json"] or "{}")
            events.append(
                TimelineEvent(
                    source="runtime_session_events",
                    event_id=f"{row['session_id']}:{row['seq']}",
                    session_id=str(row["session_id"]),
                    event_type=str(row["event_type"]),
                    message=str(row["message"]),
                    detail=detail if isinstance(detail, dict) else {},
                    created_at=str(row["created_at"]),
                )
            )
    finally:
        conn.close()
    return events


def _query_execution_facts(
    start_dt: datetime, end_dt: datetime
) -> list[TimelineEvent]:
    """Query `execution_facts` for facts within [start_dt, end_dt)."""
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()
    events: list[TimelineEvent] = []
    conn = get_connection()
    try:
        # Check if table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_facts'"
        ).fetchone()
        if not table_check:
            return []
        rows = conn.execute(
            """
            SELECT fact_id, turn_key, session_id, kind, name, ok, status,
                   detail_json, created_at
            FROM execution_facts
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at ASC, fact_id ASC
            """,
            (start_iso, end_iso),
        ).fetchall()
        for row in rows:
            detail = {}
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                detail = json.loads(row["detail_json"] or "{}")
            events.append(
                TimelineEvent(
                    source="execution_facts",
                    event_id=str(row["fact_id"]),
                    session_id=str(row["session_id"]),
                    kind=str(row["kind"]),
                    name=str(row["name"]),
                    status=str(row["status"]),
                    detail=detail if isinstance(detail, dict) else {},
                    created_at=str(row["created_at"]),
                )
            )
    finally:
        conn.close()
    return events


def _query_sessions_created(
    start_dt: datetime, end_dt: datetime
) -> list[TimelineEvent]:
    """Query `runtime_sessions` for sessions started within [start_dt, end_dt)."""
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()
    events: list[TimelineEvent] = []
    conn = get_connection()
    try:
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_sessions'"
        ).fetchone()
        if not table_check:
            return []
        rows = conn.execute(
            """
            SELECT session_id, started_at, updated_at, event_count, last_event_type,
                   last_message, request_preview, task_class, status
            FROM runtime_sessions
            WHERE started_at >= ? AND started_at < ?
            ORDER BY started_at ASC
            """,
            (start_iso, end_iso),
        ).fetchall()
        for row in rows:
            events.append(
                TimelineEvent(
                    source="runtime_sessions",
                    event_id=str(row["session_id"]),
                    session_id=str(row["session_id"]),
                    event_type="session_started",
                    message=str(row.get("last_message") or row.get("request_preview") or ""),
                    status=str(row.get("status", "")),
                    created_at=str(row["started_at"]),
                )
            )
    finally:
        conn.close()
    return events


# ── Public API ──────────────────────────────────────────────────────────────


def events_between(
    start: str | datetime,
    end: str | datetime,
    *,
    event_kinds: set[str] | None = None,
) -> TimelineResult:
    """Return all events in ``[start, end)`` from existing durable stores.

    Args:
        start: ISO-8601 UTC string or aware datetime for the range start
            (inclusive).
        end: ISO-8601 UTC string or aware datetime for the range end
            (exclusive).
        event_kinds: Optional filter on event type/kind. For
            ``runtime_session_events`` this filters ``event_type``; for
            ``execution_facts`` this filters ``kind``.

    Returns:
        A :class:`TimelineResult` containing events from
        ``runtime_session_events``, ``execution_facts``, and
        ``runtime_sessions``, ordered by ``created_at ASC``.

    Law:
        - Events at exactly *start* are included.
        - Events at exactly *end* are excluded.
        - Never fabricates missing events.
        - Results survive restart (durable SQLite).
    """
    # Normalise inputs
    if isinstance(start, str):
        start_dt = _parse_iso(start)
    else:
        start_dt = start
    if isinstance(end, str):
        end_dt = _parse_iso(end)
    else:
        end_dt = end

    if start_dt is None or end_dt is None or start_dt >= end_dt:
        return TimelineResult(
            events=[],
            range_start=str(start) if isinstance(start, str) else start.isoformat(),
            range_end=str(end) if isinstance(end, str) else end.isoformat(),
            total=0,
        )

    all_events: list[TimelineEvent] = []

    # Always query runtime_session_events
    events = _query_session_events(start_dt, end_dt)
    if event_kinds:
        events = [e for e in events if e.event_type in event_kinds]
    all_events.extend(events)

    # Always query execution_facts
    facts = _query_execution_facts(start_dt, end_dt)
    if event_kinds:
        facts = [f for f in facts if f.kind in event_kinds]
    all_events.extend(facts)

    # Sort by created_at ASC
    all_events.sort(key=lambda e: e.created_at)

    return TimelineResult(
        events=all_events,
        range_start=start_dt.isoformat(),
        range_end=end_dt.isoformat(),
        total=len(all_events),
    )


def history_on(
    date: str | datetime,
    timezone_name: str,
) -> TimelineResult:
    """Query all events for a local calendar day.

    Convenience wrapper that resolves *date* in the given *timezone_name* to a
    calendar-day UTC range, then calls ``events_between``.

    Args:
        date: An ISO date string (``2026-08-25``) or an aware datetime.
        timezone_name: IANA timezone for resolving the calendar-day boundaries.
            Must be a valid IANA timezone; raises ``ZoneInfoNotFoundError``
            otherwise.

    Returns:
        A :class:`TimelineResult` for the resolved calendar day.
    """
    from core.time_range_resolver import resolve

    tz = ZoneInfo(timezone_name)

    if isinstance(date, str):
        resolved = resolve(date, timezone_name=timezone_name)
        if isinstance(resolved, dict) and resolved.get("kind") == "UNSUPPORTED":
            return TimelineResult(events=[], range_start="", range_end="", total=0)
        if isinstance(resolved, TimeRange):
            return events_between(resolved.start_utc, resolved.end_utc)
        return TimelineResult(events=[], range_start="", range_end="", total=0)

    # datetime case: resolve to local calendar day
    local_dt = date.astimezone(tz)
    start_local = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    return events_between(start_utc, end_utc)


def latest(
    *,
    event_kinds: set[str] | None = None,
    session_id: str | None = None,
    source: str | None = None,
    limit: int = 1,
) -> list[TimelineEvent]:
    """Return the *limit* most recent matching events.

    Queries the same durable SQLite stores; no in-memory cache.

    Args:
        event_kinds: Optional filter on event type/kind.
        session_id: Optional session scope.
        source: Optional source table filter
            (``runtime_session_events``, ``execution_facts``).
        limit: Maximum events to return (default 1).

    Returns:
        Chronologically newest events first.
    """
    bounded = max(1, min(int(limit), 100))
    all_events: list[TimelineEvent] = []

    conn = get_connection()
    try:
        # Query runtime_session_events (most recent by created_at DESC)
        if source is None or source == "runtime_session_events":
            session_table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_session_events'"
            ).fetchone()
            if session_table_exists:
                sql = """
                    SELECT session_id, seq, event_type, message, details_json, created_at
                    FROM runtime_session_events
                """
                where: list[str] = []
                params: list[Any] = []
                if session_id:
                    where.append("session_id = ?")
                    params.append(session_id)
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY created_at DESC, seq DESC LIMIT ?"
                params.append(bounded * 4)  # generous fetch for potential post-filter
                rows = conn.execute(sql, params).fetchall()
                for row in rows:
                    detail = {}
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        detail = json.loads(row["details_json"] or "{}")
                    ev = TimelineEvent(
                        source="runtime_session_events",
                        event_id=f"{row['session_id']}:{row['seq']}",
                        session_id=str(row["session_id"]),
                        event_type=str(row["event_type"]),
                        message=str(row["message"]),
                        detail=detail if isinstance(detail, dict) else {},
                        created_at=str(row["created_at"]),
                    )
                    if event_kinds and ev.event_type not in event_kinds:
                        continue
                    all_events.append(ev)

        # Query execution_facts
        if source is None or source == "execution_facts":
            fact_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_facts'"
            ).fetchone()
            if fact_exists:
                sql = """
                    SELECT fact_id, turn_key, session_id, kind, name, ok, status,
                           detail_json, created_at
                    FROM execution_facts
                """
                where2: list[str] = []
                params2: list[Any] = []
                if session_id:
                    where2.append("session_id = ?")
                    params2.append(session_id)
                if where2:
                    sql += " WHERE " + " AND ".join(where2)
                sql += " ORDER BY created_at DESC, fact_id DESC LIMIT ?"
                params2.append(bounded * 4)
                rows2 = conn.execute(sql, params2).fetchall()
                for row in rows2:
                    detail = {}
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        detail = json.loads(row["detail_json"] or "{}")
                    fact = TimelineEvent(
                        source="execution_facts",
                        event_id=str(row["fact_id"]),
                        session_id=str(row["session_id"]),
                        kind=str(row["kind"]),
                        name=str(row["name"]),
                        status=str(row["status"]),
                        detail=detail if isinstance(detail, dict) else {},
                        created_at=str(row["created_at"]),
                    )
                    if event_kinds and fact.kind not in event_kinds:
                        continue
                    all_events.append(fact)

    finally:
        conn.close()

    # Sort most recent first, deduplicate by event_id, apply limit
    all_events.sort(key=lambda e: e.created_at, reverse=True)
    seen: set[str] = set()
    unique: list[TimelineEvent] = []
    for ev in all_events:
        eid = ev.event_id or ev.created_at
        if eid in seen:
            continue
        seen.add(eid)
        unique.append(ev)
        if len(unique) >= bounded:
            break

    return unique


# ── Re-export resolve_range at the module level ─────────────────────────────


def resolve_range(
    phrase: str,
    now_utc: datetime | None = None,
    timezone_name: str | None = None,
):
    """Thin re-export of :func:`core.time_range_resolver.resolve`."""
    return _resolve_range(phrase, now_utc, timezone_name)
