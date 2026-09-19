"""core/school/api.py — HTTP handlers for the school surfaces.

Mounted by core/web/api/service.py under:

    GET  /school            console page (login / admin / teacher views)
    GET  /school/student    student page
    GET  /school/api/state  console or student state (per session role)
    POST /school/api/*      operations (see _POST_HANDLERS)

Sessions ride a signed cookie set at login. Every operation re-derives the
actor from the VERIFIED session — role claims in the body are never read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.school import service, store
from core.school.session import (
    SchoolSession,
    issue_session_token,
    session_from_headers,
)

COOKIE_NAME = "vool_school_session"


def handle_school_get(path: str, query: dict, headers: dict | None):
    from core.school.pages import render_console_page, render_student_page

    session = session_from_headers(headers)
    if path == "/school/student":
        return _html(200, render_student_page(session))
    return _html(200, render_console_page(session))


def handle_school_api_get(path: str, query: dict, headers: dict | None):
    session = session_from_headers(headers)
    if path == "/school/api/state":
        if session is None:
            return _json(403, {"error": "school_session_required"})
        if session.role == "STUDENT":
            return _json(200, service.student_state(session))
        return _json(200, service.console_state(session))
    return _json(404, {"error": "not_found"})


def handle_school_api_post(path: str, body: dict, headers: dict | None, client_host: str = ""):
    handler = _POST_HANDLERS.get(path)
    if handler is None:
        return _json(404, {"error": "not_found"})
    session = session_from_headers(headers)
    return handler(body, session)


# ---- helpers -----------------------------------------------------------------


def _json(status: int, payload: dict):
    from core.web.api.service import json_response

    return json_response(status, payload)


def _html(status: int, text: str):
    from core.web.api.service import html_response

    return html_response(status, text)


def _session_cookie(token: str) -> str:
    return f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={8*3600}"


# ---- POST operations -----------------------------------------------------------


def _op_bootstrap(body: dict, session: SchoolSession | None):
    result = service.bootstrap_school(
        str(body.get("name") or "VOOL Demo School"),
        str(body.get("region") or ""),
    )
    status = 200 if not result.get("already_exists") else 409
    return _json(status, result)


def _op_login(body: dict, session: SchoolSession | None):
    code = str(body.get("access_code") or "").strip()
    user = store.find_user_by_code(code) if code else None
    if user is None:
        return _json(401, {"error": "invalid_or_used_code"})
    token = issue_session_token(
        school_id=user["school_id"],
        user_id=user["user_id"],
        role=user["role"],
        display_name=user["display_name"],
    )
    store.audit(user["school_id"], user["user_id"], "login", {})
    from core.web.api.service import json_response as _jr

    return _jr(
        200,
        {
            "token": token,
            "role": user["role"],
            "display_name": user["display_name"],
            "school_id": user["school_id"],
        },
        headers={"Set-Cookie": _session_cookie(token)},
    )


def _op_logout(body: dict, session: SchoolSession | None):
    from core.web.api.service import json_response as _jr

    return _jr(
        200,
        {"ok": True},
        headers={"Set-Cookie": f"{COOKIE_NAME}=; Path=/; Max-Age=0"},
    )


def _op_policy_set(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.set_school_policy(session, dict(body.get("policy") or {}))
    if result is None:
        return _json(403, {"error": "admin_only"})
    return _json(200, {"school": result})


def _op_provider_set(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    ok = service.set_provider(session, dict(body.get("entry") or {}))
    if not ok:
        return _json(403, {"error": "admin_only"})
    return _json(200, {"providers": store.list_providers(session.school_id)})


def _op_user_create(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    user = service.create_school_user(
        session, str(body.get("role") or ""), str(body.get("display_name") or "")
    )
    if user is None:
        return _json(403, {"error": "admin_only_or_bad_role"})
    return _json(200, user)  # includes one-time access_code


def _op_class_create(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    klass = service.create_class(
        session, str(body.get("name") or ""), str(body.get("teacher_user_id") or "")
    )
    if klass is None:
        return _json(403, {"error": "admin_only_or_bad_teacher"})
    return _json(200, {"class": klass})


def _op_enroll(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.enroll_student(
        session, str(body.get("class_id") or ""), str(body.get("student_user_id") or "")
    )
    if result is None:
        return _json(403, {"error": "admin_only_or_bad_target"})
    return _json(200, result)


def _op_lesson_start(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.start_lesson(
        session,
        str(body.get("class_id") or ""),
        str(body.get("title") or "Lesson"),
        dict(body.get("policy") or {}),
    )
    if result is None:
        return _json(403, {"error": "not_your_class"})
    if "error" in result:
        return _json(409, result)
    return _json(200, result)


def _op_lesson_end(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.end_lesson(session, str(body.get("lesson_id") or ""))
    if result is None:
        return _json(403, {"error": "not_your_class"})
    return _json(200, result)


def _op_lesson_policy(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.update_lesson_policy(
        session, str(body.get("lesson_id") or ""), dict(body.get("policy") or {})
    )
    if result is None:
        return _json(403, {"error": "not_your_class_or_lesson_inactive"})
    return _json(200, result)


def _op_assignment_send(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.send_assignment(
        session,
        str(body.get("lesson_id") or ""),
        str(body.get("title") or "Assignment"),
        str(body.get("body") or ""),
        dict(body.get("policy") or {}),
    )
    if result is None:
        return _json(403, {"error": "not_your_class_or_lesson_inactive"})
    return _json(200, result)


def _op_lesson_join(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.join_lesson(session, str(body.get("join_code") or ""))
    if "error" in result:
        return _json(403, result)
    from core.web.api.service import json_response as _jr

    return _jr(200, result, headers={"Set-Cookie": _session_cookie(result["token"])})


def _op_help(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.request_help(session, str(body.get("note") or ""))
    if result is None:
        return _json(409, {"error": "no_active_lesson"})
    return _json(200, result)


def _op_handin_prepare(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.hand_in_prepare(
        session, str(body.get("assignment_id") or ""), str(body.get("work_text") or "")
    )
    if isinstance(result, dict) and result.get("error"):
        return _json(int(result.get("status") or 403), result)
    return _json(200, result)


def _op_handin_submit(body: dict, session: SchoolSession | None):
    if session is None:
        return _json(403, {"error": "school_session_required"})
    result = service.hand_in_submit(
        session, str(body.get("assignment_id") or ""), str(body.get("work_text") or "")
    )
    if isinstance(result, dict) and result.get("error"):
        return _json(int(result.get("status") or 403), result)
    return _json(200, result)


def _op_ack(body: dict, session: SchoolSession | None):
    from core.school import submission

    if session is None or session.role not in {"TEACHER", "SCHOOL_ADMIN"}:
        return _json(403, {"error": "teacher_or_admin_only"})
    result = submission.acknowledge(
        str(body.get("submission_id") or ""), teacher_user_id=session.user_id
    )
    if result is None:
        return _json(404, {"error": "not_found"})
    return _json(200, {"submission": result})


_POST_HANDLERS: dict[str, Callable[[dict, SchoolSession | None], Any]] = {
    "/school/api/bootstrap": _op_bootstrap,
    "/school/api/login": _op_login,
    "/school/api/logout": _op_logout,
    "/school/api/policy.set": _op_policy_set,
    "/school/api/provider.set": _op_provider_set,
    "/school/api/user.create": _op_user_create,
    "/school/api/class.create": _op_class_create,
    "/school/api/enroll": _op_enroll,
    "/school/api/lesson.start": _op_lesson_start,
    "/school/api/lesson.end": _op_lesson_end,
    "/school/api/lesson.policy": _op_lesson_policy,
    "/school/api/assignment.send": _op_assignment_send,
    "/school/api/lesson.join": _op_lesson_join,
    "/school/api/help": _op_help,
    "/school/api/handin.prepare": _op_handin_prepare,
    "/school/api/handin.submit": _op_handin_submit,
    "/school/api/submission.ack": _op_ack,
}
