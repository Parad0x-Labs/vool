"""core/school/submission.py — Hand In as a governed, idempotent effect.

Prepare != submit (goal §19):

    prepare()  builds the disclosure payload — exactly what will travel, plus
               an explicit NOT-shared list — and stores nothing.
    submit()   inserts ONCE under UNIQUE(assignment_id, student_user_id,
               version); a duplicate Hand In returns the ORIGINAL submission
               read-only (idempotent reconcile, no blind duplicate). The
               submission id binds to the authenticated student principal, so
               one student cannot hand in as another.

Both student and teacher hold matching Ed25519-signed receipts (node key).
Statuses: submitted → acknowledged (teacher opens it) | unknown (ack lost —
reconciled read-only, never re-submitted blindly).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.school import assistance, store
from core.school.store import _now, new_id, school_db

NOT_SHARED_NOTE = (
    "Not shared: other conversations, personal profile, unrelated files, "
    "browsing history, credentials."
)


@dataclass(frozen=True)
class SubmissionRefusal:
    error: str
    status: int


def prepare(
    *,
    assignment_id: str,
    student_user_id: str,
    work_text: str,
) -> dict | SubmissionRefusal:
    assignment = store.get_assignment(assignment_id)
    if not assignment:
        return SubmissionRefusal("assignment not found", 404)
    student = store.get_user(student_user_id) or {}
    klass = store.get_class(assignment["class_id"]) or {}
    summary = assistance.assistance_summary(
        school_id=assignment["school_id"] if "school_id" in assignment else klass.get("school_id", ""),
        student_user_id=student_user_id,
        assignment_id=assignment_id,
    )
    return {
        "will_share": {
            "student_display_name": student.get("display_name") or "Student",
            "class": klass.get("name") or "",
            "assignment": assignment["title"],
            "work": str(work_text or "")[:20_000],
            "assistance_summary": summary,
        },
        "not_shared": NOT_SHARED_NOTE,
    }


def submit(
    *,
    assignment_id: str,
    student_user_id: str,
    work_text: str,
) -> dict | SubmissionRefusal:
    assignment = store.get_assignment(assignment_id)
    if not assignment:
        return SubmissionRefusal("assignment not found", 404)
    student = store.get_user(student_user_id) or {}
    if not student or student.get("role") != "STUDENT":
        return SubmissionRefusal("only a student may hand in", 403)
    class_id = assignment["class_id"]
    if not store.is_enrolled(class_id, student_user_id):
        return SubmissionRefusal("not enrolled in this class", 403)
    klass = store.get_class(class_id) or {}
    school_id = klass.get("school_id") or ""
    summary = assistance.assistance_summary(
        school_id=school_id, student_user_id=student_user_id, assignment_id=assignment_id
    )
    submission_id = new_id("sub")
    try:
        with school_db() as conn:
            conn.execute(
                "INSERT INTO school_submission (submission_id, school_id, class_id, assignment_id,"
                " student_user_id, version, status, work_text, assistance_json, created_at)"
                " VALUES (?,?,?,?,?,?, 'submitted', ?, ?, ?)",
                (submission_id, school_id, class_id, assignment_id, student_user_id, 1,
                 str(work_text or "")[:20_000], json.dumps(summary), _now()),
            )
    except Exception:
        # UNIQUE hit: duplicate Hand In — reconcile read-only with the original.
        existing = submission_for(assignment_id, student_user_id)
        if existing:
            return {"submission": public_submission(existing), "idempotent": True}
        return SubmissionRefusal("submission conflict", 409)
    row = submission_for(assignment_id, student_user_id) or {}
    receipt = _sign_receipt(row, summary)
    with school_db() as conn:
        conn.execute(
            "UPDATE school_submission SET receipt_json=? WHERE submission_id=?",
            (json.dumps(receipt), row["submission_id"]),
        )
    store.audit(
        school_id, student_user_id, "hand_in",
        {"assignment_id": assignment_id, "submission_id": row["submission_id"]},
    )
    row["receipt_json"] = json.dumps(receipt)
    return {"submission": public_submission(row), "idempotent": False}


def submission_for(assignment_id: str, student_user_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute(
            "SELECT * FROM school_submission WHERE assignment_id=? AND student_user_id=?"
            " ORDER BY created_at DESC LIMIT 1",
            (assignment_id, student_user_id),
        ).fetchone()
    return dict(row) if row else None


def public_submission(row: dict) -> dict:
    student = store.get_user(row.get("student_user_id") or "") or {}
    assignment = store.get_assignment(row.get("assignment_id") or "") or {}
    return {
        "submission_id": row.get("submission_id"),
        "assignment_id": row.get("assignment_id"),
        "assignment_title": assignment.get("title") or "",
        "student_display_name": student.get("display_name") or "Student",
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "acknowledged_at": row.get("acknowledged_at"),
        "work_text": row.get("work_text") or "",
        "assistance": json.loads(row.get("assistance_json") or "{}"),
        "receipt": json.loads(row["receipt_json"]) if row.get("receipt_json") else None,
    }


def submissions_for_assignment(assignment_id: str) -> list[dict]:
    with school_db() as conn:
        rows = conn.execute(
            "SELECT * FROM school_submission WHERE assignment_id=? ORDER BY created_at DESC",
            (assignment_id,),
        ).fetchall()
    return [public_submission(dict(r)) for r in rows]


def acknowledge(submission_id: str, *, teacher_user_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute(
            "SELECT * FROM school_submission WHERE submission_id=?", (submission_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE school_submission SET status='acknowledged', acknowledged_at=?"
            " WHERE submission_id=?",
            (_now(), submission_id),
        )
    store.audit(row["school_id"], teacher_user_id, "submission_acknowledged",
                {"submission_id": submission_id})
    fresh = dict(
        _fetch(submission_id) or {}
    )
    return public_submission(fresh) if fresh else None


def _fetch(submission_id: str) -> dict | None:
    with school_db() as conn:
        row = conn.execute(
            "SELECT * FROM school_submission WHERE submission_id=?", (submission_id,)
        ).fetchone()
    return dict(row) if row else None


def _sign_receipt(row: dict, summary: dict) -> dict:
    from network.signer import sign

    payload = {
        "schema": "vool.school.submission.v1",
        "submission_id": row["submission_id"],
        "school_id": row["school_id"],
        "class_id": row["class_id"],
        "assignment_id": row["assignment_id"],
        "student_user_id": row["student_user_id"],
        "created_at": row["created_at"],
        "assistance_summary": summary,
        "content_sha256": __import__("hashlib").sha256(
            (row.get("work_text") or "").encode("utf-8")
        ).hexdigest(),
    }
    from network.signer import get_local_peer_id

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        **payload,
        "issuer_peer_id": get_local_peer_id(),
        "signature": sign(canonical),  # base64 Ed25519 (network.signer format)
    }
