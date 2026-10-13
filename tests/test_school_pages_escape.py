"""School console pages must show people's names as text, never as markup.

Pins a real bug: the admin and teacher pages built table rows with innerHTML from display names,
class names, lesson titles and help notes, so a person named ``<img src=x onerror=...>`` ran
script in the admin's browser.
"""
from __future__ import annotations

import json
import types

from tests.served_browser import launch_chromium

EVIL = '<img src=x onerror="window.__pwned=1">'


def _state() -> dict:
    user = {"display_name": EVIL, "role": "TEACHER", "user_id": "u_1", "revoked": False}
    student = {"display_name": EVIL, "role": "STUDENT", "user_id": "u_2", "revoked": False}
    klass = {"name": EVIL, "class_id": "c_1"}
    lesson = {"lesson_id": "l_1", "title": EVIL, "join_code": "J1", "policy": {}}
    return {
        "school": {"name": EVIL}, "users": [user, student], "classes": [klass], "_enrolled": {"c_1": [student]},
        "providers": [{"provider_id": EVIL, "model_id": "m", "locality": "local", "enabled": True}],
        "audit": [{"ts": "t", "action": EVIL, "actor_user_id": "u_1"}], "school_policy": {},
        "teaching": [{**klass, "active_lesson": lesson, "students": [student], "assignments": [{"title": EVIL, "assignment_id": "a_1"}],
                      "help_queue": [{"ts": "t", "student": EVIL, "note": EVIL}],
                      "submissions": [{"student_display_name": EVIL, "assignment_title": EVIL, "status": "submitted", "submission_id": "s_1",
                                       "assistance": {}, "work_text": "w"}]}],
    }


def _serve(html: str):
    return lambda route: route.fulfill(status=200, content_type="text/html", body=html)


def test_admin_and_teacher_pages_render_names_as_text() -> None:
    from core.school.pages import render_console_page

    manager, browser = launch_chromium()
    try:
        for role in ("SCHOOL_ADMIN", "TEACHER"):
            html = render_console_page(types.SimpleNamespace(display_name=EVIL, role=role))
            page = browser.new_page()
            page.route("**/school/api/state", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(_state())))
            page.route("http://school.test/", _serve(html))
            page.route("**/x", lambda r: r.abort())
            page.goto("http://school.test/")
            page.wait_for_function("() => document.body.innerText.includes('onerror')")
            assert page.evaluate("() => document.querySelectorAll('img').length") == 0, role
            assert page.evaluate("() => window.__pwned") is None, role
            page.close()
    finally:
        browser.close()
        manager.stop()
