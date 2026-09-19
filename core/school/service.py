"""core/school/service.py — console operations (admin, teacher, student).

Every operation: validate the ACTING principal from the verified session,
check scope (teacher owns the class; student enrolled in the class; admin owns
the school), then act + audit. Teacher-is-not-root (goal §7): a teacher token
reaches only classes where they are the owning teacher; a student token
reaches only the active lesson of a class they are enrolled in.
"""

from __future__ import annotations

import secrets
from typing import Any

from core.school import assistance, quota, store, submission
from core.school.session import SchoolSession, issue_session_token

# ---- admin ----------------------------------------------------------------


def bootstrap_school(name: str, region: str = "") -> dict:
    """First-run: create the school + its first SCHOOL_ADMIN access code."""
    existing = store.get_first_school()
    if existing:
        return {"school": existing, "already_exists": True}
    school = store.create_school(name, region)
    admin = store.create_user(school["school_id"], "SCHOOL_ADMIN", "School Admin")
    store.audit(school["school_id"], admin["user_id"], "school_created", {"name": name})
    return {"school": school, "admin_access_code": admin["access_code"], "already_exists": False}


def set_school_policy(session: SchoolSession, policy: dict) -> dict | None:
    if session.role != "SCHOOL_ADMIN":
        return None
    store.update_school_policy(session.school_id, policy or {})
    store.audit(session.school_id, session.user_id, "school_policy_set", {"keys": sorted(policy or {})})
    return store.get_school(session.school_id)


def set_provider(session: SchoolSession, entry: dict) -> bool:
    if session.role != "SCHOOL_ADMIN":
        return False
    store.set_provider(
        session.school_id,
        str(entry.get("provider_id") or ""),
        str(entry.get("model_id") or ""),
        str(entry.get("locality") or "local"),
        bool(entry.get("enabled", True)),
        str(entry.get("note") or ""),
    )
    store.audit(session.school_id, session.user_id, "provider_set", {
        "provider_id": str(entry.get("provider_id") or ""),
        "model_id": str(entry.get("model_id") or ""),
        "locality": str(entry.get("locality") or "local"),
        "enabled": bool(entry.get("enabled", True)),
    })
    return True


def create_school_user(session: SchoolSession, role: str, display_name: str) -> dict | None:
    if session.role != "SCHOOL_ADMIN" or role not in {"SCHOOL_ADMIN", "TEACHER", "STUDENT"}:
        return None
    user = store.create_user(session.school_id, role, display_name)
    store.audit(session.school_id, session.user_id, "user_created",
                {"role": role, "user_id": user["user_id"]})
    return user  # carries the one-time access_code


def create_class(session: SchoolSession, name: str, teacher_user_id: str) -> dict | None:
    if session.role != "SCHOOL_ADMIN":
        return None
    teacher = store.get_user(teacher_user_id) or {}
    if teacher.get("role") != "TEACHER" or teacher.get("school_id") != session.school_id:
        return None
    klass = store.create_class(session.school_id, name, teacher_user_id)
    store.audit(session.school_id, session.user_id, "class_created",
                {"class_id": klass["class_id"], "teacher_user_id": teacher_user_id})
    return klass


def enroll_student(session: SchoolSession, class_id: str, student_user_id: str) -> dict | None:
    if session.role != "SCHOOL_ADMIN":
        return None
    klass = store.get_class(class_id) or {}
    student = store.get_user(student_user_id) or {}
    if not klass or klass.get("school_id") != session.school_id:
        return None
    if student.get("role") != "STUDENT" or student.get("school_id") != session.school_id:
        return None
    result = store.enroll(class_id, student_user_id)
    store.audit(session.school_id, session.user_id, "student_enrolled",
                {"class_id": class_id, "student_user_id": student_user_id})
    return result


def revoke_user(session: SchoolSession, user_id: str) -> bool:
    if session.role != "SCHOOL_ADMIN":
        return False
    target = store.get_user(user_id) or {}
    if target.get("school_id") != session.school_id:
        return False
    store.revoke_user(user_id)
    store.audit(session.school_id, session.user_id, "user_revoked", {"user_id": user_id})
    return True


# ---- teacher ----------------------------------------------------------------


def _owns_class(session: SchoolSession, class_id: str) -> bool:
    if session.role == "SCHOOL_ADMIN":
        klass = store.get_class(class_id) or {}
        return klass.get("school_id") == session.school_id
    if session.role != "TEACHER":
        return False
    klass = store.get_class(class_id) or {}
    return klass.get("teacher_user_id") == session.user_id


def start_lesson(
    session: SchoolSession, class_id: str, title: str, policy: dict
) -> dict | None:
    if session.role not in {"TEACHER", "SCHOOL_ADMIN"} or not _owns_class(session, class_id):
        return None
    existing = store.active_lesson_for_class(class_id)
    if existing:
        return {"error": "a lesson is already active for this class", "lesson": existing}
    join_code = secrets.token_hex(4)
    lesson = store.start_lesson(class_id, session.user_id, title, policy or {}, join_code)
    store.audit(session.school_id, session.user_id, "lesson_started",
                {"lesson_id": lesson["lesson_id"], "class_id": class_id,
                 "policy_version": lesson.get("policy_version", 1)})
    return {"lesson": lesson}


def end_lesson(session: SchoolSession, lesson_id: str) -> dict | None:
    lesson = store.get_lesson(lesson_id) or {}
    if not lesson:
        return None
    if session.role not in {"TEACHER", "SCHOOL_ADMIN"} or not _owns_class(session, lesson["class_id"]):
        return None
    ended = store.end_lesson(lesson_id)
    store.audit(session.school_id, session.user_id, "lesson_ended", {"lesson_id": lesson_id})
    return {"lesson": ended}


def update_lesson_policy(session: SchoolSession, lesson_id: str, policy: dict) -> dict | None:
    lesson = store.get_lesson(lesson_id) or {}
    if not lesson or lesson.get("status") != "active":
        return None
    if session.role not in {"TEACHER", "SCHOOL_ADMIN"} or not _owns_class(session, lesson["class_id"]):
        return None
    updated = store.update_lesson_policy(lesson_id, policy or {})
    store.audit(session.school_id, session.user_id, "lesson_policy_updated",
                {"lesson_id": lesson_id, "policy_version": updated.get("policy_version")})
    return {"lesson": updated}


def send_assignment(
    session: SchoolSession, lesson_id: str, title: str, body: str, policy: dict
) -> dict | None:
    lesson = store.get_lesson(lesson_id) or {}
    if not lesson or lesson.get("status") != "active":
        return None
    if session.role not in {"TEACHER", "SCHOOL_ADMIN"} or not _owns_class(session, lesson["class_id"]):
        return None
    assignment = store.create_assignment(lesson_id, lesson["class_id"], title, body, policy or {})
    store.audit(session.school_id, session.user_id, "assignment_sent",
                {"assignment_id": assignment["assignment_id"], "lesson_id": lesson_id})
    return {"assignment": assignment}


# ---- student ----------------------------------------------------------------


def join_lesson(session: SchoolSession, join_code: str) -> dict:
    """Redeem a lesson join code: enrolls-on-the-fly into the lesson's class? NO.

    The student must already be enrolled by the admin; the code only BINDS the
    student's session to the lesson (bounded, expiring authority — goal §7).
    """
    if session.role != "STUDENT":
        return {"error": "only students join lessons"}
    lesson = store.find_lesson_by_join_code(join_code)
    if not lesson:
        return {"error": "lesson code invalid or lesson ended"}
    if not store.is_enrolled(lesson["class_id"], session.user_id):
        return {"error": "you are not enrolled in this class"}
    token = issue_session_token(
        school_id=session.school_id,
        user_id=session.user_id,
        role="STUDENT",
        display_name=session.display_name,
        lesson_id=lesson["lesson_id"],
        ttl_seconds=4 * 3600,
    )
    store.audit(session.school_id, session.user_id, "lesson_joined",
                {"lesson_id": lesson["lesson_id"]})
    return {"token": token, "lesson": lesson}


def request_help(session: SchoolSession, note: str) -> dict | None:
    lesson = store.active_lesson_for_student(session.user_id)
    if not lesson:
        return None
    assignment = store.latest_assignment_for_student(session.user_id) or {}
    assistance.record_event(
        school_id=session.school_id,
        lesson_id=lesson["lesson_id"],
        assignment_id=assignment.get("assignment_id") or "",
        student_user_id=session.user_id,
        event="help_requested",
        detail={"note": str(note or "")[:300]},
    )
    store.audit(session.school_id, session.user_id, "help_requested",
                {"lesson_id": lesson["lesson_id"]})
    return {"ok": True, "lesson_id": lesson["lesson_id"]}


def hand_in_prepare(session: SchoolSession, assignment_id: str, work_text: str) -> dict | Any:
    if session.role != "STUDENT":
        return {"error": "only students hand in", "status": 403}
    result = submission.prepare(
        assignment_id=assignment_id, student_user_id=session.user_id, work_text=work_text
    )
    return result


def hand_in_submit(session: SchoolSession, assignment_id: str, work_text: str) -> dict | Any:
    if session.role != "STUDENT":
        return {"error": "only students hand in", "status": 403}
    result = submission.submit(
        assignment_id=assignment_id, student_user_id=session.user_id, work_text=work_text
    )
    return result


# ---- console state -----------------------------------------------------------


def console_state(session: SchoolSession) -> dict:
    school = store.get_school(session.school_id) or {}
    state: dict[str, Any] = {
        "school": {"school_id": session.school_id, "name": school.get("name") or ""},
        "me": {"user_id": session.user_id, "role": session.role,
               "display_name": session.display_name},
    }
    if session.role == "SCHOOL_ADMIN":
        state["users"] = store.list_users(session.school_id)
        state["classes"] = store.list_classes(session.school_id)
        state["providers"] = store.list_providers(session.school_id)
        state["school_policy"] = school.get("policy") or {}
        state["audit"] = store.list_audit(session.school_id, limit=100)
    if session.role in {"TEACHER", "SCHOOL_ADMIN"}:
        classes = store.list_classes(session.school_id)
        if session.role == "TEACHER":
            classes = [c for c in classes if c.get("teacher_user_id") == session.user_id]
        view = []
        for klass in classes:
            lesson = store.active_lesson_for_class(klass["class_id"])
            assignments = (
                store.assignments_for_lesson(lesson["lesson_id"]) if lesson else []
            )
            subs = []
            for asg in assignments:
                for s in submission.submissions_for_assignment(asg["assignment_id"]):
                    subs.append(
                        {
                            "assignment_title": s["assignment_title"],
                            "student_display_name": s["student_display_name"],
                            "status": s["status"],
                            "created_at": s["created_at"],
                            "submission_id": s["submission_id"],
                            "assistance": s["assistance"],
                        }
                    )
            view.append(
                {
                    "class_id": klass["class_id"],
                    "name": klass["name"],
                    "students": store.class_students(klass["class_id"]),
                    "active_lesson": (
                        {
                            "lesson_id": lesson["lesson_id"],
                            "title": lesson["title"],
                            "join_code": lesson["join_code"],
                            "policy": lesson["policy"],
                            "started_at": lesson["started_at"],
                        }
                        if lesson
                        else None
                    ),
                    "assignments": assignments,
                    "submissions": subs,
                    "help_queue": _help_queue(klass["class_id"]),
                }
            )
        state["teaching"] = view
        state["_enrolled"] = {
            klass["class_id"]: store.class_students(klass["class_id"])
            for klass in store.list_classes(session.school_id)
        }
    return state


def _help_queue(class_id: str) -> list[dict]:
    with store.school_db() as conn:
        rows = conn.execute(
            "SELECT a.ts, a.detail, u.display_name FROM school_audit a"
            " JOIN school_user u ON u.user_id = a.actor_user_id"
            " JOIN school_enrollment e ON e.user_id = a.actor_user_id AND e.class_id = ?"
            " WHERE a.action='help_requested' ORDER BY a.ts DESC LIMIT 20",
            (class_id,),
        ).fetchall()
    import json as _json

    out = []
    for r in rows:
        detail = _json.loads(r["detail"] or "{}")
        out.append({"ts": r["ts"], "student": r["display_name"], "note": detail.get("note", "")})
    return out


def student_state(session: SchoolSession) -> dict:
    lesson = store.active_lesson_for_student(session.user_id)
    assignment = store.latest_assignment_for_student(session.user_id)
    from core.school.policy import resolve_policy

    policy = resolve_policy(
        school_id=session.school_id,
        class_id=(lesson or {}).get("class_id", ""),
        lesson_id=(lesson or {}).get("lesson_id", ""),
        assignment_id=(assignment or {}).get("assignment_id", ""),
        student_user_id=session.user_id,
    )
    klass = store.get_class((lesson or {}).get("class_id", "")) or {}
    return {
        "me": {"display_name": session.display_name, "role": session.role},
        "class": klass.get("name") or "",
        "lesson": (
            {"title": lesson["title"], "started_at": lesson["started_at"]}
            if lesson
            else None
        ),
        "assignment": (
            {
                "assignment_id": assignment["assignment_id"],
                "title": assignment["title"],
                "body": assignment["body"],
            }
            if assignment
            else None
        ),
        "policy": policy.to_context(),
        "assistance_level_name": assistance.level_name(policy.max_assistance),
        "quota": quota.quota_verdict(session.school_id, session.user_id).__dict__,
    }
