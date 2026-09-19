"""core/school/store.py — durable school state (additive, lazily ensured).

All tables are prefixed ``school_`` and created on first use with IF NOT EXISTS:
an existing VOOL install keeps its data untouched, and a PERSONAL-edition
install never touches them at all. The main runtime SQLite connection
(storage.db.get_connection) is reused — one database, additive surface.

IDs are opaque stable handles (``sch_``, ``usr_``, ...). Display names are
presentation attributes only; authority and quota key on the opaque ids.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS school_school (
    school_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    policy_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS school_user (
    user_id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    access_code_hash TEXT NOT NULL,
    access_code_plain TEXT NOT NULL DEFAULT '',
    code_redeemed INTEGER NOT NULL DEFAULT 0,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS school_class (
    class_id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL,
    name TEXT NOT NULL,
    teacher_user_id TEXT NOT NULL,
    policy_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS school_enrollment (
    enrollment_id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(class_id, user_id)
);
CREATE TABLE IF NOT EXISTS school_lesson (
    lesson_id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL,
    teacher_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    join_code TEXT NOT NULL DEFAULT '',
    policy_json TEXT NOT NULL DEFAULT '{}',
    policy_version INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS school_assignment (
    assignment_id TEXT PRIMARY KEY,
    lesson_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    policy_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS school_provider (
    school_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    locality TEXT NOT NULL DEFAULT 'local',
    enabled INTEGER NOT NULL DEFAULT 1,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (school_id, provider_id, model_id)
);
CREATE TABLE IF NOT EXISTS school_quota_usage (
    usage_day TEXT NOT NULL,
    school_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (usage_day, school_id, user_id)
);
CREATE TABLE IF NOT EXISTS school_assistance_event (
    event_id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL DEFAULT '',
    student_user_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS school_submission (
    submission_id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    assignment_id TEXT NOT NULL,
    student_user_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'submitted',
    work_text TEXT NOT NULL DEFAULT '',
    assistance_json TEXT NOT NULL DEFAULT '{}',
    receipt_json TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    UNIQUE(assignment_id, student_user_id, version)
);
CREATE TABLE IF NOT EXISTS school_audit (
    audit_id TEXT PRIMARY KEY,
    school_id TEXT NOT NULL,
    actor_user_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS school_lesson_class ON school_lesson(class_id);
CREATE INDEX IF NOT EXISTS school_assign_lesson ON school_assignment(lesson_id);
CREATE INDEX IF NOT EXISTS school_assist_student ON school_assistance_event(student_user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


@contextmanager
def school_db() -> Iterator[sqlite3.Connection]:
    """One connection, row access by name, commit on success."""
    conn = get_connection()
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# ---- school ---------------------------------------------------------------


def create_school(name: str, region: str = "", policy: dict | None = None) -> dict:
    school_id = new_id("sch")
    with school_db() as conn:
        conn.execute(
            "INSERT INTO school_school (school_id, name, region, policy_json, created_at) VALUES (?,?,?,?,?)",
            (school_id, str(name or "School")[:120], str(region or "")[:60],
             json.dumps(policy or {}), _now()),
        )
    return get_school(school_id) or {}


def get_school(school_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute("SELECT * FROM school_school WHERE school_id=?", (school_id,)).fetchone()
    d = _row_to_dict(row)
    if d:
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
    return d


def get_first_school() -> dict | None:
    with school_db() as conn:
        row = conn.execute("SELECT * FROM school_school ORDER BY created_at LIMIT 1").fetchone()
    d = _row_to_dict(row)
    if d:
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
    return d


def update_school_policy(school_id: str, policy: dict) -> None:
    with school_db() as conn:
        conn.execute(
            "UPDATE school_school SET policy_json=? WHERE school_id=?",
            (json.dumps(policy or {}), school_id),
        )


# ---- users -----------------------------------------------------------------


def create_user(
    school_id: str, role: str, display_name: str, *, teacher_user_id: str = ""
) -> dict:
    import hashlib

    assert role in {"SCHOOL_ADMIN", "TEACHER", "STUDENT"}, role
    user_id = new_id("usr")
    code = secrets.token_hex(4)  # 8-char single-use access code, shown once
    with school_db() as conn:
        conn.execute(
            "INSERT INTO school_user (user_id, school_id, role, display_name, access_code_hash,"
            " access_code_plain, code_redeemed, revoked, created_at) VALUES (?,?,?,?,?,?,0,0,?)",
            (user_id, school_id, role, str(display_name or "")[:80],
             hashlib.sha256(code.encode()).hexdigest(), "", _now()),
        )
    d = get_user(user_id) or {}
    d["access_code"] = code  # shown ONCE at creation; never again
    return d


def get_user(user_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute("SELECT * FROM school_user WHERE user_id=?", (user_id,)).fetchone()
    d = _row_to_dict(row)
    if d:
        d.pop("access_code_plain", None)
        d.pop("access_code_hash", None)
    return d


def find_user_by_code(code: str) -> dict | None:
    """Redeem-or-reject in ONE transaction: a code works exactly once."""
    import hashlib

    digest = hashlib.sha256(str(code or "").strip().encode()).hexdigest()
    with school_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM school_user WHERE access_code_hash=? AND code_redeemed=0 AND revoked=0",
            (digest,),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            "UPDATE school_user SET code_redeemed=1 WHERE user_id=?", (row["user_id"],)
        )
        conn.execute("COMMIT")
    d = dict(row)
    d.pop("access_code_hash", None)
    d.pop("access_code_plain", None)
    return d


def revoke_user(user_id: str) -> None:
    with school_db() as conn:
        conn.execute("UPDATE school_user SET revoked=1 WHERE user_id=?", (user_id,))


def list_users(school_id: str) -> list[dict]:
    with school_db() as conn:
        rows = conn.execute(
            "SELECT user_id, school_id, role, display_name, revoked, created_at"
            " FROM school_user WHERE school_id=? ORDER BY created_at",
            (school_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- classes / enrollment ---------------------------------------------------


def create_class(school_id: str, name: str, teacher_user_id: str, policy: dict | None = None) -> dict:
    class_id = new_id("cls")
    with school_db() as conn:
        conn.execute(
            "INSERT INTO school_class (class_id, school_id, name, teacher_user_id, policy_json, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (class_id, school_id, str(name or "Class")[:80], teacher_user_id,
             json.dumps(policy or {}), _now()),
        )
    d = get_class(class_id) or {}
    return d


def get_class(class_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute("SELECT * FROM school_class WHERE class_id=?", (class_id,)).fetchone()
    d = _row_to_dict(row)
    if d:
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
    return d


def list_classes(school_id: str) -> list[dict]:
    with school_db() as conn:
        rows = conn.execute(
            "SELECT * FROM school_class WHERE school_id=? ORDER BY created_at", (school_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
        out.append(d)
    return out


def enroll(class_id: str, user_id: str) -> dict:
    enrollment_id = new_id("enr")
    with school_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO school_enrollment (enrollment_id, class_id, user_id, created_at)"
            " VALUES (?,?,?,?)",
            (enrollment_id, class_id, user_id, _now()),
        )
    return {"enrollment_id": enrollment_id, "class_id": class_id, "user_id": user_id}


def class_students(class_id: str) -> list[dict]:
    with school_db() as conn:
        rows = conn.execute(
            "SELECT u.user_id, u.display_name, u.revoked FROM school_enrollment e"
            " JOIN school_user u ON u.user_id = e.user_id"
            " WHERE e.class_id=? AND u.role='STUDENT' ORDER BY u.created_at",
            (class_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def is_enrolled(class_id: str, user_id: str) -> bool:
    with school_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM school_enrollment WHERE class_id=? AND user_id=?",
            (class_id, user_id),
        ).fetchone()
    return row is not None


# ---- lessons ----------------------------------------------------------------


def start_lesson(
    class_id: str, teacher_user_id: str, title: str, policy: dict, join_code: str
) -> dict:
    lesson_id = new_id("lsn")
    with school_db() as conn:
        conn.execute(
            "INSERT INTO school_lesson (lesson_id, class_id, teacher_user_id, title, status,"
            " join_code, policy_json, policy_version, started_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (lesson_id, class_id, teacher_user_id, str(title or "Lesson")[:120], "active",
             join_code, json.dumps(policy or {}), 1, _now()),
        )
    return get_lesson(lesson_id) or {}


def get_lesson(lesson_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute("SELECT * FROM school_lesson WHERE lesson_id=?", (lesson_id,)).fetchone()
    d = _row_to_dict(row)
    if d:
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
    return d


def active_lesson_for_class(class_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute(
            "SELECT * FROM school_lesson WHERE class_id=? AND status='active'"
            " ORDER BY started_at DESC LIMIT 1",
            (class_id,),
        ).fetchone()
    d = _row_to_dict(row)
    if d:
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
    return d


def find_lesson_by_join_code(code: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute(
            "SELECT * FROM school_lesson WHERE join_code=? AND status='active'",
            (str(code or "").strip(),),
        ).fetchone()
    d = _row_to_dict(row)
    if d:
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
    return d


def update_lesson_policy(lesson_id: str, policy: dict) -> dict:
    with school_db() as conn:
        conn.execute(
            "UPDATE school_lesson SET policy_json=?, policy_version=policy_version+1 WHERE lesson_id=?",
            (json.dumps(policy or {}), lesson_id),
        )
    return get_lesson(lesson_id) or {}


def end_lesson(lesson_id: str) -> dict:
    with school_db() as conn:
        conn.execute(
            "UPDATE school_lesson SET status='ended', ended_at=? WHERE lesson_id=?",
            (_now(), lesson_id),
        )
    return get_lesson(lesson_id) or {}


def active_lesson_for_student(user_id: str) -> dict | None:
    """The one active lesson of a class this student is enrolled in (most recent)."""
    with school_db() as conn:
        rows = conn.execute(
            "SELECT l.* FROM school_lesson l"
            " JOIN school_enrollment e ON e.class_id = l.class_id"
            " WHERE e.user_id=? AND l.status='active'"
            " ORDER BY l.started_at DESC LIMIT 1",
            (user_id,),
        ).fetchall()
    for row in rows:
        d = dict(row)
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
        return d
    return None


# ---- assignments -------------------------------------------------------------


def create_assignment(
    lesson_id: str, class_id: str, title: str, body: str, policy: dict
) -> dict:
    assignment_id = new_id("asg")
    with school_db() as conn:
        conn.execute(
            "INSERT INTO school_assignment (assignment_id, lesson_id, class_id, title, body,"
            " policy_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (assignment_id, lesson_id, class_id, str(title or "Assignment")[:120],
             str(body or "")[:4000], json.dumps(policy or {}), _now()),
        )
    return get_assignment(assignment_id) or {}


def get_assignment(assignment_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute(
            "SELECT * FROM school_assignment WHERE assignment_id=?", (assignment_id,)
        ).fetchone()
    d = _row_to_dict(row)
    if d:
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
    return d


def assignments_for_lesson(lesson_id: str) -> list[dict]:
    with school_db() as conn:
        rows = conn.execute(
            "SELECT * FROM school_assignment WHERE lesson_id=? ORDER BY created_at", (lesson_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["policy"] = json.loads(d.pop("policy_json") or "{}")
        out.append(d)
    return out


def latest_assignment_for_student(user_id: str) -> dict | None:
    lesson = active_lesson_for_student(user_id)
    if not lesson:
        return None
    items = assignments_for_lesson(lesson["lesson_id"])
    return items[-1] if items else None


# ---- providers --------------------------------------------------------------


def set_provider(
    school_id: str, provider_id: str, model_id: str, locality: str, enabled: bool, note: str = ""
) -> None:
    with school_db() as conn:
        conn.execute(
            "INSERT INTO school_provider (school_id, provider_id, model_id, locality, enabled, note)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(school_id, provider_id, model_id)"
            " DO UPDATE SET locality=excluded.locality, enabled=excluded.enabled, note=excluded.note",
            (school_id, str(provider_id), str(model_id), str(locality or "local"),
             1 if enabled else 0, str(note or "")[:200]),
        )


def list_providers(school_id: str) -> list[dict]:
    with school_db() as conn:
        rows = conn.execute(
            "SELECT provider_id, model_id, locality, enabled, note FROM school_provider"
            " WHERE school_id=? ORDER BY provider_id, model_id",
            (school_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- audit --------------------------------------------------------------------


def audit(school_id: str, actor_user_id: str, action: str, detail: dict | None = None) -> None:
    with school_db() as conn:
        conn.execute(
            "INSERT INTO school_audit (audit_id, school_id, actor_user_id, action, detail, ts)"
            " VALUES (?,?,?,?,?,?)",
            (new_id("aud"), school_id, str(actor_user_id or ""), str(action or "")[:80],
             json.dumps(detail or {}), _now()),
        )


def list_audit(school_id: str, limit: int = 200) -> list[dict]:
    with school_db() as conn:
        rows = conn.execute(
            "SELECT * FROM school_audit WHERE school_id=? ORDER BY ts DESC LIMIT ?",
            (school_id, int(limit)),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail") or "{}")
        out.append(d)
    return out
