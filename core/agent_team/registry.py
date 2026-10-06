"""The team registry: the one durable record of what the coordinator started.

SQLite in the team's own directory, WAL mode, append-only events. Status shown to the user is
read from here — never from an agent's own text. A coordinator that restarts opens the same file
and re-adopts each agent only when the process it recorded is still the same process: same pid
AND same create time. Anything else is reported lost. Nothing is ever relaunched by recovery,
so a restart can never double-run an agent or double-spend its budget.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS team (
    team_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    workspace TEXT NOT NULL,
    limits TEXT NOT NULL,
    state TEXT NOT NULL,
    coordinator_pid INTEGER,
    coordinator_create_time REAL
);
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    title TEXT NOT NULL,
    importance TEXT NOT NULL,
    effective_importance TEXT NOT NULL,
    depth INTEGER NOT NULL,
    parent_id TEXT NOT NULL DEFAULT '',
    contract TEXT NOT NULL,
    state TEXT NOT NULL,
    run_token TEXT NOT NULL,
    pid INTEGER,
    create_time REAL,
    launched_at REAL,
    ended_at REAL,
    exit_code INTEGER,
    progress_done INTEGER NOT NULL DEFAULT 0,
    progress_planned INTEGER NOT NULL DEFAULT 0,
    running_seconds REAL NOT NULL DEFAULT 0,
    spend TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT '{}',
    pause_reason TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    agents TEXT NOT NULL,
    paths TEXT NOT NULL,
    evidence TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    alert TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    team_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE TRIGGER IF NOT EXISTS events_append_only_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_append_only_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""

#: Agent states. ``partial`` is a cap hit with work kept; ``lost`` is a recorded process
#: that is gone (or is now a different process) after a coordinator restart.
LIVE_STATES = ("pending", "running", "paused")
END_STATES = ("done", "failed", "partial", "stopped", "lost", "needs_decision", "refused", "unverified")

_JSON_COLUMNS = {"contract", "spend", "result"}


class Registry:
    def __init__(self, team_dir: str | Path) -> None:
        self.team_dir = Path(team_dir)
        self.team_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.team_dir / "team.sqlite3"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ team
    def create_team(self, team_id: str, *, workspace: str, limits: dict[str, Any],
                    coordinator: tuple[int, float]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO team VALUES (?,?,?,?,?,?,?)",
                (team_id, time.time(), workspace, json.dumps(limits), "running",
                 coordinator[0], coordinator[1]),
            )
            self.event(team_id, "team_created", workspace=workspace, limits=limits)

    def team(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM team LIMIT 1").fetchone()
        if row is None:
            return None
        out = dict(row)
        out["limits"] = json.loads(out["limits"])
        return out

    def set_team(self, team_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE team SET {cols} WHERE team_id=?", (*fields.values(), team_id))

    # ---------------------------------------------------------------- agents
    def add_agent(self, row: dict[str, Any]) -> None:
        payload = {k: (json.dumps(v) if k in _JSON_COLUMNS else v) for k, v in row.items()}
        cols = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        with self._lock:
            self._conn.execute(f"INSERT INTO agents ({cols}) VALUES ({marks})", tuple(payload.values()))

    def update_agent(self, agent_id: str, **fields: Any) -> None:
        if not fields:
            return
        payload = {k: (json.dumps(v) if k in _JSON_COLUMNS else v) for k, v in fields.items()}
        cols = ", ".join(f"{k}=?" for k in payload)
        with self._lock:
            self._conn.execute(f"UPDATE agents SET {cols} WHERE agent_id=?", (*payload.values(), agent_id))

    @staticmethod
    def _agent_row(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for key in _JSON_COLUMNS:
            out[key] = json.loads(out.get(key) or "{}")
        return out

    def agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return None if row is None else self._agent_row(row)

    def agents(self, states: Iterable[str] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if states is None:
                rows = self._conn.execute("SELECT * FROM agents ORDER BY rowid").fetchall()
            else:
                wanted = tuple(states)
                marks = ",".join("?" for _ in wanted)
                rows = self._conn.execute(
                    f"SELECT * FROM agents WHERE state IN ({marks}) ORDER BY rowid", wanted
                ).fetchall()
        return [self._agent_row(r) for r in rows]

    # ------------------------------------------------------------- conflicts
    def add_conflict(self, row: dict[str, Any]) -> None:
        payload = {k: (json.dumps(v) if not isinstance(v, (str, int, float)) else v) for k, v in row.items()}
        cols = ", ".join(payload)
        marks = ", ".join("?" for _ in payload)
        with self._lock:
            self._conn.execute(f"INSERT INTO conflicts ({cols}) VALUES ({marks})", tuple(payload.values()))

    def update_conflict(self, conflict_id: str, **fields: Any) -> None:
        payload = {k: (json.dumps(v) if not isinstance(v, (str, int, float)) else v) for k, v in fields.items()}
        cols = ", ".join(f"{k}=?" for k in payload)
        with self._lock:
            self._conn.execute(f"UPDATE conflicts SET {cols} WHERE conflict_id=?", (*payload.values(), conflict_id))

    def conflicts(self, state: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if state is None:
                rows = self._conn.execute("SELECT * FROM conflicts ORDER BY created_at").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM conflicts WHERE state=? ORDER BY created_at", (state,)
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            for key in ("agents", "paths", "evidence", "recommendation", "decision"):
                item[key] = json.loads(item[key])
            out.append(item)
        return out

    # ---------------------------------------------------------------- events
    def event(self, team_id: str, kind: str, agent_id: str = "", **data: Any) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, team_id, agent_id, kind, data) VALUES (?,?,?,?,?)",
                (time.time(), team_id, agent_id, kind, json.dumps(data, default=str)),
            )
            return int(cur.lastrowid or 0)

    def events(self, after: int = 0, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if kind is None:
                rows = self._conn.execute("SELECT * FROM events WHERE seq>? ORDER BY seq", (after,)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE seq>? AND kind=? ORDER BY seq", (after, kind)
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item["data"])
            out.append(item)
        return out


__all__ = ["END_STATES", "LIVE_STATES", "Registry"]
