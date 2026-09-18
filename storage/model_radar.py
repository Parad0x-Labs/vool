"""Model Radar persistence — the operator's own evidence ledger.

Three laws shape this store:

1. The BEFORE side of every qualification is read from here. A feed supplies the
   present; only the operator's own recorded past can measure a discount against
   it — which is what makes fabricated discounts structurally impossible.
2. Findings are append-only rows keyed by a content fingerprint. They survive
   provider outages untouched (last-known-good) because the card renders from the
   row alone.
3. A dismissal is permanent for that fingerprint. It is recorded with its own
   timestamp so "never nag" is auditable, not a UI memory.

Tables are created lazily in-module (the same pattern as storage/cloud_model_catalog)
so the radar adds no migration-order dependency for unrelated callers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.model_radar import (
    ModelObservation,
    RadarFinding,
    RadarPreferences,
)
from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    return get_connection()


def _init_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS model_radar_observations (
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            evidence_fetched_at TEXT NOT NULL,
            source_feed TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (provider_id, model_id)
        );
        CREATE TABLE IF NOT EXISTS model_radar_findings (
            fingerprint TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            viewed_at TEXT,
            dismissed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_model_radar_findings_model
          ON model_radar_findings(provider_id, model_id, created_at);
        CREATE TABLE IF NOT EXISTS model_radar_dismissals (
            fingerprint TEXT PRIMARY KEY,
            dismissed_at TEXT NOT NULL,
            origin TEXT NOT NULL DEFAULT 'ui'
        );
        CREATE TABLE IF NOT EXISTS model_radar_prefs (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_radar_conflicts (
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            detected_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_radar_try_once (
            token TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            session_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT
        );
        """
    )
    conn.commit()


# ---- observations (the recorded baseline) ---------------------------------------------


def record_observations(provider_id: str, observations) -> int:
    """Upsert the provider's snapshot rows as the recorded present."""
    rows = []
    now = _utcnow()
    for observation in observations:
        if observation.provider_id != provider_id:
            raise ValueError("observation provider mismatch")
        rows.append(
            (
                observation.provider_id,
                observation.model_id,
                json.dumps(observation.to_dict(), sort_keys=True, separators=(",", ":")),
                observation.evidence_fetched_at,
                observation.source_feed,
                now,
            )
        )
    conn = _connect()
    try:
        _init_tables(conn)
        conn.executemany(
            """INSERT INTO model_radar_observations
               (provider_id, model_id, payload_json, evidence_fetched_at, source_feed, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider_id, model_id) DO UPDATE SET
                 payload_json=excluded.payload_json,
                 evidence_fetched_at=excluded.evidence_fetched_at,
                 source_feed=excluded.source_feed,
                 updated_at=excluded.updated_at""",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_observation(provider_id: str, model_id: str) -> ModelObservation | None:
    conn = _connect()
    try:
        _init_tables(conn)
        row = conn.execute(
            "SELECT payload_json FROM model_radar_observations WHERE provider_id = ? AND model_id = ?",
            (provider_id, model_id),
        ).fetchone()
        return ModelObservation.from_dict(json.loads(row["payload_json"])) if row else None
    finally:
        conn.close()


def observations_for_identity(provider_id: str, model_id: str) -> list[ModelObservation]:
    """Every recorded observation row for one identity (single-row store today;
    the list keeps conflict checks honest if history rows are added later)."""
    observation = get_observation(provider_id, model_id)
    return [observation] if observation is not None else []


# ---- findings --------------------------------------------------------------------------


def finding_exists(fingerprint: str) -> bool:
    conn = _connect()
    try:
        _init_tables(conn)
        row = conn.execute(
            "SELECT 1 FROM model_radar_findings WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def insert_finding(finding: RadarFinding) -> None:
    conn = _connect()
    try:
        _init_tables(conn)
        conn.execute(
            """INSERT OR IGNORE INTO model_radar_findings
               (fingerprint, kind, provider_id, model_id, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                finding.fingerprint,
                finding.kind,
                finding.provider_id,
                finding.model_id,
                json.dumps(finding.to_dict(), sort_keys=True, separators=(",", ":")),
                finding.created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_findings(
    *,
    include_read: bool = True,
    include_dismissed: bool = False,
    limit: int = 50,
) -> list[RadarFinding]:
    conn = _connect()
    try:
        _init_tables(conn)
        where = []
        if not include_read:
            where.append("viewed_at IS NULL")
        if not include_dismissed:
            where.append("dismissed_at IS NULL")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT payload_json FROM model_radar_findings{clause} ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [RadarFinding.from_dict(json.loads(row["payload_json"])) for row in rows]
    finally:
        conn.close()


def unread_findings(limit: int = 50) -> list[RadarFinding]:
    return list_findings(include_read=False, include_dismissed=False, limit=limit)


def mark_viewed(fingerprints) -> int:
    fingerprints = [str(f) for f in fingerprints if str(f).strip()]
    if not fingerprints:
        return 0
    now = _utcnow()
    conn = _connect()
    try:
        _init_tables(conn)
        conn.executemany(
            "UPDATE model_radar_findings SET viewed_at = ? WHERE fingerprint = ? AND viewed_at IS NULL",
            [(now, f) for f in fingerprints],
        )
        conn.commit()
        return len(fingerprints)
    finally:
        conn.close()


def dismiss_finding(fingerprint: str, *, now: str | None = None, origin: str = "ui") -> bool:
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        return False
    moment = str(now or _utcnow())
    conn = _connect()
    try:
        _init_tables(conn)
        row = conn.execute(
            "SELECT 1 FROM model_radar_findings WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "UPDATE model_radar_findings SET dismissed_at = ? WHERE fingerprint = ?",
            (moment, fingerprint),
        )
        conn.execute(
            """INSERT INTO model_radar_dismissals (fingerprint, dismissed_at, origin)
               VALUES (?, ?, ?)
               ON CONFLICT(fingerprint) DO UPDATE SET dismissed_at=excluded.dismissed_at""",
            (fingerprint, moment, origin),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def is_dismissed(fingerprint: str) -> bool:
    conn = _connect()
    try:
        _init_tables(conn)
        row = conn.execute(
            "SELECT 1 FROM model_radar_dismissals WHERE fingerprint = ?", (str(fingerprint or "").strip(),)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def last_issued_at(provider_id: str, model_id: str) -> str | None:
    """The created_at of the most recent UNDISMISSED finding for one identity."""
    conn = _connect()
    try:
        _init_tables(conn)
        row = conn.execute(
            """SELECT created_at FROM model_radar_findings
               WHERE provider_id = ? AND model_id = ? AND dismissed_at IS NULL
               ORDER BY created_at DESC LIMIT 1""",
            (provider_id, model_id),
        ).fetchone()
        return str(row["created_at"]) if row else None
    finally:
        conn.close()


# ---- preferences ------------------------------------------------------------------------


def load_preferences() -> RadarPreferences:
    conn = _connect()
    try:
        _init_tables(conn)
        row = conn.execute("SELECT payload_json FROM model_radar_prefs WHERE id = 1").fetchone()
        if row is None:
            return RadarPreferences()
        return RadarPreferences.from_dict(json.loads(row["payload_json"]))
    except Exception:
        return RadarPreferences()
    finally:
        conn.close()


def save_preferences_row(preferences: RadarPreferences) -> RadarPreferences:
    conn = _connect()
    try:
        _init_tables(conn)
        conn.execute(
            """INSERT INTO model_radar_prefs (id, payload_json) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json""",
            (json.dumps(preferences.to_dict(), sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()
    finally:
        conn.close()
    stored = load_preferences()
    return stored


# ---- conflicts --------------------------------------------------------------------------


def record_conflict(provider_id: str, model_id: str, payload: dict[str, Any]) -> None:
    conn = _connect()
    try:
        _init_tables(conn)
        conn.execute(
            "INSERT INTO model_radar_conflicts (provider_id, model_id, payload_json, detected_at) VALUES (?, ?, ?, ?)",
            (
                provider_id,
                model_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_conflicts(limit: int = 20) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        _init_tables(conn)
        rows = conn.execute(
            "SELECT payload_json, detected_at FROM model_radar_conflicts ORDER BY detected_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        out = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["detected_at"] = row["detected_at"]
            out.append(payload)
        return out
    except Exception:
        return []
    finally:
        conn.close()


# ---- try-once grants ---------------------------------------------------------------------


def insert_try_once(
    *, token: str, fingerprint: str, session_id: str, provider_id: str, model_id: str,
    granted_at: str, expires_at: str,
) -> None:
    conn = _connect()
    try:
        _init_tables(conn)
        conn.execute(
            """INSERT OR REPLACE INTO model_radar_try_once
               (token, fingerprint, session_id, provider_id, model_id, granted_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token, fingerprint, session_id, provider_id, model_id, granted_at, expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_try_once(token: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        _init_tables(conn)
        row = conn.execute(
            "SELECT * FROM model_radar_try_once WHERE token = ?", (str(token or "").strip(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def consume_try_once(token: str, *, now: str | None = None) -> bool:
    conn = _connect()
    try:
        _init_tables(conn)
        cursor = conn.execute(
            "UPDATE model_radar_try_once SET consumed_at = ? WHERE token = ? AND consumed_at IS NULL",
            (str(now or _utcnow()), str(token or "").strip()),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


__all__ = [
    "consume_try_once",
    "dismiss_finding",
    "finding_exists",
    "get_observation",
    "get_try_once",
    "insert_finding",
    "insert_try_once",
    "is_dismissed",
    "last_issued_at",
    "list_conflicts",
    "list_findings",
    "load_preferences",
    "mark_viewed",
    "observations_for_identity",
    "record_conflict",
    "record_observations",
    "save_preferences_row",
    "unread_findings",
]
