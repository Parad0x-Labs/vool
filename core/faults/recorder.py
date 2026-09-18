"""The durable fault ledger: best-effort recording, typed lifecycle, derived reads.

Recording a fault must never be able to fail the thing that faulted. Like the
execution-fact ledger this module's store pattern follows, ``record_fault`` is
fail-open (an unrecordable fault returns "" rather than raising), deterministic ids
make a re-recorded fault a no-op instead of a duplicate, and every read derives from
the one table so two surfaces cannot disagree about what was filed.

Three things make this a plane and not a log:

* the record arrives TYPED and COMPLETE (see ``core.faults.records``) -- the store
  does not assemble meaning from columns it happens to find;
* the turn/session identity is resolved through ``core.execution_truth``'s one
  canonical resolver, so a fault row joins to the execution facts of the same turn
  with zero per-producer guesswork;
* a security-relevant fault OPENS ITS SECURITY OBSERVATION here, at the same
  instant, with the fault id carried as evidence -- the linkage is derived from the
  catalog's ``security_relevant`` declaration, so there is no second list to drift.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from core.faults.catalog import (
    FaultVocabularyError,
    allowed_fault_transition,
    get_spec,
)
from core.faults.records import FaultCause, FaultRecord

_LOCK = threading.RLock()

#: Row keys the store persists, in canonical order. ``dedupe`` is stored so a fault's
#: identity argument survives with the row (it is part of what the id hashes).
_ROW_KEYS = (
    "fault_id",
    "code",
    "schema_version",
    "category",
    "severity",
    "retry",
    "lifecycle",
    "user_message",
    "operator_action",
    "authority",
    "turn_key",
    "attempt_id",
    "effect_id",
    "session_id",
    "dedupe",
    "evidence_refs",
    "context",
    "cause_chain",
    "created_at",
    "updated_at",
)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS fault_records (
    fault_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    retry TEXT NOT NULL DEFAULT '',
    lifecycle TEXT NOT NULL DEFAULT 'raised',
    user_message TEXT NOT NULL DEFAULT '',
    operator_action TEXT NOT NULL DEFAULT '',
    authority TEXT NOT NULL DEFAULT '',
    turn_key TEXT NOT NULL DEFAULT '',
    attempt_id TEXT NOT NULL DEFAULT '',
    effect_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    dedupe TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    context_json TEXT NOT NULL DEFAULT '{}',
    cause_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
)
"""


def _conn():
    from core.runtime_continuity import _conn as _runtime_conn

    return _runtime_conn()


def _ensure_table(conn) -> bool:
    try:
        conn.execute(_CREATE_SQL)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fault_records_turn ON fault_records(turn_key, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fault_records_code ON fault_records(code, created_at)")
        return True
    except sqlite3.Error:
        return False


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _row_for(record: FaultRecord) -> dict[str, Any]:
    return {
        "fault_id": record.fault_id,
        "code": record.code,
        "schema_version": record.schema_version,
        "category": record.category,
        "severity": record.severity,
        "retry": record.retry,
        "lifecycle": record.lifecycle,
        "user_message": record.user_message,
        "operator_action": record.operator_action,
        "authority": record.authority,
        "turn_key": record.turn_key,
        "attempt_id": record.attempt_id,
        "effect_id": record.effect_id,
        "session_id": record.session_id,
        "dedupe": "",
        "evidence_refs": json.dumps(list(record.evidence_refs), ensure_ascii=True),
        "context": json.dumps(record.context, sort_keys=True, ensure_ascii=True, default=str),
        "cause_chain": json.dumps(
            [cause.to_dict() for cause in record.cause_chain], ensure_ascii=True, default=str
        ),
        "created_at": record.created_at,
        "updated_at": record.created_at,
    }


def _persist_row(conn, row: dict[str, Any]) -> None:
    """The one INSERT. Module-level so tests can prove recording survives a broken store."""
    conn.execute(
        """
        INSERT OR IGNORE INTO fault_records (
            fault_id, code, schema_version, category, severity, retry, lifecycle,
            user_message, operator_action, authority, turn_key, attempt_id, effect_id,
            session_id, dedupe, evidence_json, context_json, cause_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(row[key] for key in _ROW_KEYS),
    )


def _record_from_row(row: Any) -> FaultRecord:
    try:
        evidence = json.loads(row["evidence_json"] or "[]")
    except Exception:
        evidence = []
    try:
        context = json.loads(row["context_json"] or "{}")
    except Exception:
        context = {}
    try:
        cause_rows = json.loads(row["cause_json"] or "[]")
    except Exception:
        cause_rows = []
    return FaultRecord(
        fault_id=str(row["fault_id"]),
        code=str(row["code"]),
        schema_version=str(row["schema_version"]),
        category=str(row["category"]),
        severity=str(row["severity"]),
        retry=str(row["retry"]),
        lifecycle=str(row["lifecycle"]),
        user_message=str(row["user_message"]),
        operator_action=str(row["operator_action"]),
        authority=str(row["authority"]),
        turn_key=str(row["turn_key"]),
        attempt_id=str(row["attempt_id"]),
        effect_id=str(row["effect_id"]),
        session_id=str(row["session_id"]),
        evidence_refs=tuple(str(ref) for ref in (evidence if isinstance(evidence, list) else [])),
        context=dict(context) if isinstance(context, dict) else {},
        cause_chain=tuple(
            FaultCause(
                exception_type=str(frame.get("exception_type") or ""),
                message=str(frame.get("message") or ""),
            )
            for frame in (cause_rows if isinstance(cause_rows, list) else [])
            if isinstance(frame, dict)
        ),
        created_at=str(row["created_at"]),
    )


def identity_from_context(source_context: dict[str, Any] | None) -> tuple[str, str]:
    """The (turn_key, session_id) pair every producer resolves through ONE helper.

    The turn key is ``core.execution_truth.resolve_turn_key`` -- the same canonical
    identity the execution facts use, which is what makes fault rows joinable to the
    executions they belong to.
    """
    context = source_context if isinstance(source_context, dict) else {}
    from core.execution_truth import resolve_turn_key

    turn = resolve_turn_key(context, None)
    session = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    return turn, session


def _open_security_observation(record: FaultRecord) -> None:
    """A security-relevant fault opens its matching observation, same instant, one mapping."""
    try:
        from core.security_events.records import SecurityEvent
        from core.security_events.store import record_security_event

        record_security_event(SecurityEvent.for_fault(record))
    except Exception:
        # The observation is derived, never load-bearing: a failure to file it must
        # not turn the fault's own recording into a raise.
        pass


def record_fault(record: FaultRecord) -> str:
    """Persist one typed fault. Returns its id, or "" when nothing could be recorded.

    Fail-open by law: observability must not be able to fail a turn. The security
    observation for a security-relevant fault is filed here, once, at the same instant.
    """
    if not isinstance(record, FaultRecord):
        return ""
    row = _row_for(record)
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
    if get_spec(record.code).security_relevant:
        _open_security_observation(record)
    return record.fault_id


def record_for_exception(
    exc: BaseException,
    *,
    authority: str,
    turn_key: str = "",
    attempt_id: str = "",
    effect_id: str = "",
    session_id: str = "",
    evidence_refs: tuple[str, ...] = (),
    context: dict[str, Any] | None = None,
    dedupe: str = "",
) -> str:
    """Map one exception at its owning boundary and file the record. Returns the fault id."""
    from core.faults.mapping import map_exception

    record = map_exception(
        exc,
        authority=authority,
        turn_key=turn_key,
        attempt_id=attempt_id,
        effect_id=effect_id,
        session_id=session_id,
        evidence_refs=evidence_refs,
        context=context,
        dedupe=dedupe,
    )
    return record_fault(record)


def _faults_from_rows(rows: list[Any]) -> list[FaultRecord]:
    return [_record_from_row(row) for row in rows]


def faults_for_turn(turn_key: str, *, session_id: str = "", limit: int = 100) -> list[FaultRecord]:
    """Every fault filed for one turn, oldest first. The read every surface shares."""
    turn = str(turn_key or "").strip()
    if not turn:
        return []
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return []
                sql = "SELECT * FROM fault_records WHERE turn_key = ?"
                args: list[Any] = [turn]
                if str(session_id or "").strip():
                    sql += " AND session_id = ?"
                    args.append(str(session_id).strip())
                sql += " ORDER BY created_at ASC, fault_id ASC LIMIT ?"
                args.append(max(1, int(limit)))
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
    except Exception:
        return []
    return _faults_from_rows(rows)


def list_faults(*, code: str = "", turn_key: str = "", limit: int = 200) -> list[FaultRecord]:
    """Recent faults, optionally scoped by code or turn -- for turn-less producers and operator views."""
    scope_code = str(code or "").strip()
    scope_turn = str(turn_key or "").strip()
    if scope_turn:
        return faults_for_turn(scope_turn, limit=limit)
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return []
                if scope_code:
                    sql = "SELECT * FROM fault_records WHERE code = ? ORDER BY created_at DESC, fault_id DESC LIMIT ?"
                    args: list[Any] = [scope_code, max(1, int(limit))]
                else:
                    sql = "SELECT * FROM fault_records ORDER BY created_at DESC, fault_id DESC LIMIT ?"
                    args = [max(1, int(limit))]
                rows = conn.execute(sql, args).fetchall()
            finally:
                conn.close()
    except Exception:
        return []
    return _faults_from_rows(rows)


def fault_by_id(fault_id: str) -> FaultRecord | None:
    key = str(fault_id or "").strip()
    if not key:
        return None
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return None
                row = conn.execute(
                    "SELECT * FROM fault_records WHERE fault_id = ?", (key,)
                ).fetchone()
            finally:
                conn.close()
    except Exception:
        return None
    return _record_from_row(row) if row is not None else None


def advance_lifecycle(fault_id: str, new_lifecycle: str, *, actor: str = "") -> FaultRecord | None:
    """Advance a fault through the typed lifecycle; an illegal move is a typed refusal.

    Returns the updated record, or None when the fault does not exist (or the store is
    unreadable -- reads fail open, the TRANSITION itself never silently bends).
    """
    from core.faults.catalog import InvalidFaultTransitionError

    current = fault_by_id(fault_id)
    if current is None:
        return None
    target = str(new_lifecycle or "").strip()
    if not allowed_fault_transition(current.lifecycle, target):
        raise InvalidFaultTransitionError(
            f"the fault lifecycle cannot move from {current.lifecycle!r} to {target!r}"
        )
    try:
        with _LOCK:
            conn = _conn()
            try:
                if not _ensure_table(conn):
                    return None
                conn.execute(
                    "UPDATE fault_records SET lifecycle = ?, updated_at = ? WHERE fault_id = ?",
                    (target, _utcnow(), current.fault_id),
                )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error:
        return None
    return fault_by_id(fault_id)


__all__ = [
    "FaultVocabularyError",
    "advance_lifecycle",
    "fault_by_id",
    "faults_for_turn",
    "identity_from_context",
    "list_faults",
    "record_fault",
    "record_for_exception",
]
