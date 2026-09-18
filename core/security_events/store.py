"""The durable security-event ledger: append observations, advance them typed, export safe.

The store follows the execution-fact ledger's discipline: fail-open appends (an
unrecordable observation returns "" rather than breaking the turn that produced the
fault), deterministic ids make a re-recorded observation a no-op, and every read
derives from the one table. Transitions are NOT fail-open -- acknowledging or
resolving is an operator act, and an illegal act is a typed refusal, never a silent
state bend.

The export is the privacy boundary: it carries the typed fields and nothing else --
no context blobs, no raw fault material, no free-text beyond the vocabulary's own
summaries and the operator's own acknowledgement/resolution notes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from core.security_events.catalog import STATE_ACKNOWLEDGED, STATE_RESOLVED
from core.security_events.records import (
    InvalidSecurityStateError,
    SecurityEvent,
)

_LOCK = threading.RLock()

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS security_events (
    event_id TEXT PRIMARY KEY,
    sec_code TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'observed',
    source TEXT NOT NULL DEFAULT '',
    resource_class TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    fault_id TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    turn_key TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL DEFAULT '',
    acknowledged_at TEXT NOT NULL DEFAULT '',
    acknowledged_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT '',
    resolution_note TEXT NOT NULL DEFAULT ''
)
"""


def _conn():
    from core.runtime_continuity import _conn as _runtime_conn

    return _runtime_conn()


def _ensure_table(conn) -> bool:
    try:
        conn.execute(_CREATE_SQL)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_events_turn ON security_events(turn_key, observed_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_events_state ON security_events(state, observed_at)"
        )
        return True
    except sqlite3.Error:
        return False


def _row_for(event: SecurityEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "sec_code": event.sec_code,
        "schema_version": event.schema_version,
        "severity": event.severity,
        "state": event.state,
        "source": event.source,
        "resource_class": event.resource_class,
        "summary": event.summary,
        "fault_id": event.fault_id,
        "evidence_refs": json.dumps(list(event.evidence_refs), ensure_ascii=True),
        "turn_key": event.turn_key,
        "session_id": event.session_id,
        "observed_at": event.observed_at,
        "acknowledged_at": event.acknowledged_at,
        "acknowledged_by": event.acknowledged_by,
        "resolved_at": event.resolved_at,
        "resolution_note": event.resolution_note,
    }


def _persist_row(conn, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO security_events (
            event_id, sec_code, schema_version, severity, state, source, resource_class,
            summary, fault_id, evidence_json, turn_key, session_id, observed_at,
            acknowledged_at, acknowledged_by, resolved_at, resolution_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(row[key] for key in (
            "event_id", "sec_code", "schema_version", "severity", "state", "source",
            "resource_class", "summary", "fault_id", "evidence_refs", "turn_key",
            "session_id", "observed_at", "acknowledged_at", "acknowledged_by",
            "resolved_at", "resolution_note",
        )),
    )


def _event_from_row(row: Any) -> SecurityEvent:
    try:
        evidence = json.loads(row["evidence_json"] or "[]")
    except Exception:
        evidence = []
    return SecurityEvent(
        event_id=str(row["event_id"]),
        sec_code=str(row["sec_code"]),
        schema_version=str(row["schema_version"]),
        severity=str(row["severity"]),
        state=str(row["state"]),
        source=str(row["source"]),
        resource_class=str(row["resource_class"]),
        summary=str(row["summary"]),
        fault_id=str(row["fault_id"]),
        evidence_refs=tuple(str(ref) for ref in (evidence if isinstance(evidence, list) else [])),
        turn_key=str(row["turn_key"]),
        session_id=str(row["session_id"]),
        observed_at=str(row["observed_at"]),
        acknowledged_at=str(row["acknowledged_at"]),
        acknowledged_by=str(row["acknowledged_by"]),
        resolved_at=str(row["resolved_at"]),
        resolution_note=str(row["resolution_note"]),
    )


def record_security_event(event: SecurityEvent) -> str:
    """Persist one observation. Returns its id, or "" when nothing could be recorded."""
    if not isinstance(event, SecurityEvent):
        return ""
    row = _row_for(event)
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return ""
                _persist_row(conn, row)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return ""
    return event.event_id


def _load(event_id: str) -> SecurityEvent | None:
    key = str(event_id or "").strip()
    if not key:
        return None
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return None
                row = conn.execute(
                    "SELECT * FROM security_events WHERE event_id = ?", (key,)
                ).fetchone()
            finally:
                conn.close()
    except Exception:
        return None
    return _event_from_row(row) if row is not None else None


def security_event_by_id(event_id: str) -> SecurityEvent | None:
    return _load(event_id)


def _advance(event_id: str, target: str, *, actor: str = "", note: str = "") -> SecurityEvent:
    current = _load(event_id)
    if current is None:
        raise InvalidSecurityStateError(f"no security event {event_id!r} to advance")
    updated = current.with_state(target, actor=actor, note=note)
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    raise InvalidSecurityStateError("the security-event store is unavailable")
                conn.execute(
                    """
                    UPDATE security_events
                    SET state = ?, acknowledged_at = ?, acknowledged_by = ?,
                        resolved_at = ?, resolution_note = ?
                    WHERE event_id = ?
                    """,
                    (
                        updated.state,
                        updated.acknowledged_at,
                        updated.acknowledged_by,
                        updated.resolved_at,
                        updated.resolution_note,
                        updated.event_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error as exc:
        raise InvalidSecurityStateError(f"the security-event store refused the write: {exc}") from exc
    return updated


def acknowledge_security_event(event_id: str, *, by: str = "") -> SecurityEvent:
    """An operator has seen this observation. Illegal from any state but observed."""
    return _advance(event_id, STATE_ACKNOWLEDGED, actor=by)


def resolve_security_event(event_id: str, *, note: str = "") -> SecurityEvent:
    """Close the observation with a note. Requires it to have been seen first."""
    return _advance(event_id, STATE_RESOLVED, note=note)


def events_for_turn(turn_key: str, *, session_id: str = "", limit: int = 100) -> list[SecurityEvent]:
    """Every observation filed for one turn, oldest first."""
    turn = str(turn_key or "").strip()
    if not turn:
        return []
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return []
                sql = "SELECT * FROM security_events WHERE turn_key = ?"
                args: list[Any] = [turn]
                if str(session_id or "").strip():
                    sql += " AND session_id = ?"
                    args.append(str(session_id).strip())
                sql += " ORDER BY observed_at ASC, event_id ASC LIMIT ?"
                args.append(max(1, int(limit)))
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
    except Exception:
        return []
    return [_event_from_row(row) for row in rows]


def list_security_events(*, state: str = "", limit: int = 200) -> list[SecurityEvent]:
    """Recent observations, newest first, optionally scoped to one state."""
    scope_state = str(state or "").strip()
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return []
                if scope_state:
                    sql = "SELECT * FROM security_events WHERE state = ? ORDER BY observed_at DESC, event_id DESC LIMIT ?"
                    args: list[Any] = [scope_state, max(1, int(limit))]
                else:
                    sql = "SELECT * FROM security_events ORDER BY observed_at DESC, event_id DESC LIMIT ?"
                    args = [max(1, int(limit))]
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
    except Exception:
        return []
    return [_event_from_row(row) for row in rows]


def export_security_events(*, limit: int = 200) -> dict[str, Any]:
    """The privacy-safe export: typed fields only, self-describing, JSON round-trippable."""
    events = list_security_events(limit=max(1, int(limit)))
    return {
        "schema": "vool.security_event.v1",
        "event_count": len(events),
        "events": [event.to_dict() for event in events],
    }


__all__ = [
    "acknowledge_security_event",
    "events_for_turn",
    "export_security_events",
    "list_security_events",
    "record_security_event",
    "resolve_security_event",
    "security_event_by_id",
]
