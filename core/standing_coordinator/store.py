"""Durable state: watches, the action ledger, the event trail and the user's decisions.

One SQLite file under ``data/standing_coordinator/``, so a restart resumes every watch
exactly where it was.

Actions are claimed before they run. ``claim`` inserts the action's key with status
``intent``; a key that is already present means the action was taken (or attempted)
before, and it is not taken again. After the effect, the row becomes ``done``,
``refused`` or ``unsupported``. A row still at ``intent`` after a crash is marked
``uncertain`` by :meth:`CoordinatorStore.reconcile` and is never replayed: an unknown
outcome is reported, not repeated.

Decisions are append-only. Recording a new answer to the same topic supersedes the old
row (it stays, with who replaced it and when); forgetting one supersedes it with
nothing. A lookup returns only live, standing decisions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.standing_coordinator.model import StandingCoordinatorError, WatchPolicy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    team_id TEXT PRIMARY KEY,
    policy_json TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS actions (
    action_key TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    finished_at REAL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS actions_by_team ON actions (team_id, kind, agent_id);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    team_id TEXT NOT NULL,
    type TEXT NOT NULL,
    fields_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_by_team ON events (team_id, seq);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    answer TEXT NOT NULL,
    standing INTEGER NOT NULL,
    question TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    recorded_at REAL NOT NULL,
    superseded_at REAL,
    superseded_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS decisions_live ON decisions (topic_key, scope, superseded_at);
"""

#: Any scope: a decision the user said holds for every team.
SCOPE_ALL = "*"

FINAL_STATUSES = frozenset({"done", "pending", "refused", "unsupported", "not_found", "failed"})


def default_db_path() -> Path:
    from core.runtime_paths import data_path

    out = Path(data_path("standing_coordinator"))
    out.mkdir(parents=True, exist_ok=True)
    return out / "coordinator.sqlite3"


class CoordinatorStore:
    def __init__(self, path: Path | str | None = None, *, clock=time.time) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 10000")
            yield conn
        finally:
            conn.close()

    # ---------------------------------------------------------------- watches
    def add_watch(self, team_id: str, policy: WatchPolicy, *, session_id: str = "") -> bool:
        """Start watching. Returns False when the team is already watched (the policy is kept)."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT ended_at FROM watches WHERE team_id = ?", (team_id,)).fetchone()
            if row is not None and row["ended_at"] is None:
                return False
            conn.execute(
                "INSERT OR REPLACE INTO watches (team_id, policy_json, session_id, created_at, ended_at, end_reason)"
                " VALUES (?, ?, ?, ?, NULL, '')",
                (team_id, json.dumps(policy.as_row(), sort_keys=True), str(session_id or ""), self._clock()),
            )
        self.append_event(team_id, "watch_started", policy=policy.as_row())
        return True

    def end_watch(self, team_id: str, reason: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE watches SET ended_at = ?, end_reason = ? WHERE team_id = ? AND ended_at IS NULL",
                (self._clock(), str(reason or ""), team_id),
            )
            ended = cursor.rowcount == 1
        if ended:
            self.append_event(team_id, "watch_ended", reason=reason)
        return ended

    def active_watches(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM watches WHERE ended_at IS NULL ORDER BY created_at"
            ).fetchall()
        return [self._watch_row(row) for row in rows]

    def watch(self, team_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM watches WHERE team_id = ?", (team_id,)).fetchone()
        return self._watch_row(row) if row is not None else None

    @staticmethod
    def _watch_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["policy"] = WatchPolicy.from_row(json.loads(data.pop("policy_json") or "{}"))
        return data

    # ---------------------------------------------------------------- actions
    def claim(self, key: str, *, team_id: str, kind: str, agent_id: str = "", covers: tuple[str, ...] = ()) -> bool:
        """Claim an action and its covered keys in one transaction. False: already taken."""
        now = self._clock()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                taken = conn.execute("SELECT 1 FROM actions WHERE action_key = ?", (key,)).fetchone()
                if taken is not None:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "INSERT INTO actions (action_key, team_id, kind, agent_id, status, created_at)"
                    " VALUES (?, ?, ?, ?, 'intent', ?)",
                    (key, team_id, kind, agent_id, now),
                )
                for covered in covers:
                    conn.execute(
                        "INSERT OR IGNORE INTO actions (action_key, team_id, kind, agent_id, status, created_at, finished_at, detail_json)"
                        " VALUES (?, ?, 'covered', '', 'done', ?, ?, ?)",
                        (covered, team_id, now, now, json.dumps({"by": key})),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return True

    def finish(self, key: str, status: str, **detail: Any) -> None:
        if status not in FINAL_STATUSES:
            raise StandingCoordinatorError(f"unknown action status: {status}")
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE actions SET status = ?, finished_at = ?, detail_json = ? WHERE action_key = ?",
                (status, self._clock(), json.dumps(detail, sort_keys=True, default=str), key),
            )

    def release(self, key: str, covers: tuple[str, ...] = ()) -> None:
        """Give a claim back. Only for effects that are idempotent by their own key (a
        notification item deduplicated on the same key), so a retry can never double them."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM actions WHERE action_key = ?", (key,))
            for covered in covers:
                conn.execute("DELETE FROM actions WHERE action_key = ? AND kind = 'covered'", (covered,))

    def taken(self, key: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM actions WHERE action_key = ?", (key,)).fetchone() is not None

    def actions(self, team_id: str, *, kind: str | None = None, agent_id: str | None = None,
                key_prefix: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM actions WHERE team_id = ?"
        args: list[Any] = [team_id]
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        if agent_id is not None:
            sql += " AND agent_id = ?"
            args.append(agent_id)
        if key_prefix is not None:
            sql += " AND substr(action_key, 1, ?) = ?"
            args.extend([len(key_prefix), key_prefix])
        sql += " ORDER BY created_at, rowid"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["detail"] = json.loads(data.pop("detail_json") or "{}")
            out.append(data)
        return out

    def reconcile(self, *, older_than_seconds: float = 120.0) -> int:
        """Mark actions a crash left at ``intent`` as ``uncertain``. They are never replayed."""
        cutoff = self._clock() - float(older_than_seconds)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT action_key, team_id, kind, agent_id FROM actions WHERE status = 'intent' AND created_at <= ?",
                (cutoff,),
            ).fetchall()
            conn.execute(
                "UPDATE actions SET status = 'uncertain', finished_at = ? WHERE status = 'intent' AND created_at <= ?",
                (self._clock(), cutoff),
            )
        for row in rows:
            self.append_event(row["team_id"], "action_uncertain", action_key=row["action_key"],
                              kind=row["kind"], agent_id=row["agent_id"])
        return len(rows)

    # ----------------------------------------------------------------- events
    def append_event(self, team_id: str, event_type: str, **fields: Any) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO events (ts, team_id, type, fields_json) VALUES (?, ?, ?, ?)",
                (self._clock(), team_id, str(event_type), json.dumps(fields, sort_keys=True, default=str)),
            )
            return int(cursor.lastrowid)

    def events(self, team_id: str, *, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE team_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (team_id, max(0, int(after)), max(1, min(int(limit), 500))),
            ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["fields"] = json.loads(data.pop("fields_json") or "{}")
            out.append(data)
        return out

    def last_event(self, team_id: str, event_type: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE team_id = ? AND type = ? ORDER BY seq DESC LIMIT 1",
                (team_id, event_type),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["fields"] = json.loads(data.pop("fields_json") or "{}")
        return data

    # -------------------------------------------------------------- decisions
    def record_decision(self, *, scope: str, topic_key: str, answer: str, standing: bool,
                        question: str = "", source: str = "") -> dict[str, Any]:
        topic = str(topic_key or "").strip()
        text = str(answer or "").strip()
        clean_scope = str(scope or "").strip()
        if not topic:
            raise StandingCoordinatorError("a decision needs a topic key")
        if not text:
            raise StandingCoordinatorError("a decision needs an answer")
        if not clean_scope:
            raise StandingCoordinatorError("a decision needs a scope")
        decision_id = "dec-" + uuid.uuid4().hex[:16]
        now = self._clock()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if standing:
                    conn.execute(
                        "UPDATE decisions SET superseded_at = ?, superseded_by = ?"
                        " WHERE topic_key = ? AND scope = ? AND standing = 1 AND superseded_at IS NULL",
                        (now, decision_id, topic, clean_scope),
                    )
                conn.execute(
                    "INSERT INTO decisions (decision_id, scope, topic_key, answer, standing, question, source, recorded_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (decision_id, clean_scope, topic, text[:2000], 1 if standing else 0,
                     str(question or "")[:1000], str(source or "")[:200], now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.decision(decision_id) or {}

    def decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return self._decision_row(row) if row is not None else None

    def lookup_decision(self, topic_key: str, team_id: str) -> dict[str, Any] | None:
        """The live standing decision for this topic: the team's own first, else an any-team one."""
        topic = str(topic_key or "").strip()
        if not topic:
            return None
        with self._connect() as conn:
            for scope in (team_id, SCOPE_ALL):
                row = conn.execute(
                    "SELECT * FROM decisions WHERE topic_key = ? AND scope = ? AND standing = 1"
                    " AND superseded_at IS NULL ORDER BY recorded_at DESC LIMIT 1",
                    (topic, scope),
                ).fetchone()
                if row is not None:
                    return self._decision_row(row)
        return None

    def forget_decision(self, decision_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE decisions SET superseded_at = ?, superseded_by = 'forgotten'"
                " WHERE decision_id = ? AND superseded_at IS NULL",
                (self._clock(), decision_id),
            )
            return cursor.rowcount == 1

    def decisions(self, *, include_superseded: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM decisions"
        if not include_superseded:
            sql += " WHERE superseded_at IS NULL"
        sql += " ORDER BY recorded_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, (max(1, min(int(limit), 1000)),)).fetchall()
        return [self._decision_row(row) for row in rows]

    @staticmethod
    def _decision_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["standing"] = bool(data["standing"])
        return data


__all__ = ["SCOPE_ALL", "CoordinatorStore", "default_db_path"]
