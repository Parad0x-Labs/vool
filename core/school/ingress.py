"""core/school/ingress.py — the school chat gates at the /api/chat boundary.

SCHOOL edition only: every chat turn (both /api/chat and /v1/chat/completions)
crosses :func:`ingress_gate` BEFORE any model spend, and the buffered student
lane crosses :func:`post_turn` before the response is published.

ingress_gate:
    1. require a verified school session (fail closed 403);
    2. resolve the frozen EffectiveSchoolPolicy for the principal
       (student → active lesson + latest assignment; teacher/admin → school);
    3. students: atomically reserve a quota slot (429 on exhaustion);
    4. stamp ``source_context["school_policy"]`` (a RESERVED trust key — a
       client-supplied copy was stripped at intake) so the routing mint, the
       prohibition mint and the provider directive all read server truth;
    5. students are forced onto the buffered lane (stream=False): the
       assistance gate must see the whole response before it is published.

post_turn (buffered student lane):
    a. assistance publication gate (ceiling) — refuse + replace + record;
    b. quota settle (estimated tokens — honest, documented);
    c. assistance ledger event for the receipt.
"""

from __future__ import annotations

from typing import Any

from core.product_edition import is_school
from core.school import assistance, quota, service, store
from core.school.policy import EffectiveSchoolPolicy, resolve_policy
from core.school.session import SchoolSession, session_from_headers

SCHOOL_POLICY_KEY = "school_policy"


def _resolve_for_session(session: SchoolSession) -> EffectiveSchoolPolicy:
    if session.role == "STUDENT":
        lesson = store.active_lesson_for_student(session.user_id)
        assignment = store.latest_assignment_for_student(session.user_id) or {}
        return resolve_policy(
            school_id=session.school_id,
            class_id=(lesson or {}).get("class_id", ""),
            lesson_id=(lesson or {}).get("lesson_id", ""),
            assignment_id=assignment.get("assignment_id", ""),
            student_user_id=session.user_id,
        )
    return resolve_policy(school_id=session.school_id)


def ingress_gate(
    *,
    headers: dict | None,
    source_context: dict[str, Any],
    client_host: str,
) -> tuple[dict | None, dict[str, Any]]:
    """Returns ``(error_response_or_None, school_context)``.

    ``school_context`` carries the session + resolved policy for the caller to
    stamp under the reserved key. On error the turn does not run at all.
    """
    if not is_school():
        return None, {}
    session = session_from_headers(headers)
    if session is None:
        return (
            {
                "error": "school_session_required",
                "detail": "VOOL School requires a signed school session for chat.",
                "status": 403,
            },
            {},
        )
    policy = _resolve_for_session(session)
    if session.role == "STUDENT":
        verdict = quota.reserve(session.school_id, session.user_id)
        if not verdict.allowed:
            return (
                {
                    "error": "school_quota_exhausted",
                    "detail": verdict.reason,
                    "requests_used": verdict.requests_used,
                    "requests_cap": verdict.requests_cap,
                    "status": 429,
                },
                {},
            )
    school_ctx = {
        "session": {
            "user_id": session.user_id,
            "role": session.role,
            "display_name": session.display_name,
            "school_id": session.school_id,
            "lesson_id": session.lesson_id,
        },
        "policy": policy.to_context(),
    }
    return None, school_ctx


def force_buffered(school_ctx: dict[str, Any]) -> bool:
    """Students publish through the gated buffered lane."""
    role = str(((school_ctx.get("session") or {}).get("role")) or "")
    return role == "STUDENT"


def post_turn(
    *,
    school_ctx: dict[str, Any],
    result: dict[str, Any],
    user_text: str,
) -> None:
    """Buffered student lane: gate, settle, record. Mutates result in place."""
    session = school_ctx.get("session") or {}
    policy_ctx = school_ctx.get("policy") or {}
    if str(session.get("role") or "") != "STUDENT":
        return
    response_text = str((result or {}).get("response") or "")
    ceiling = int(policy_ctx.get("max_assistance") or 10)
    assessment = bool(policy_ctx.get("assessment"))
    verdict = assistance.gate_response(response_text, ceiling, assessment=assessment)
    if not verdict.publish:
        result["response"] = verdict.replacement
        result["school_assistance_violation"] = True
        assistance.record_event(
            school_id=str(session.get("school_id") or ""),
            lesson_id=str(policy_ctx.get("lesson_id") or ""),
            assignment_id=str(policy_ctx.get("assignment_id") or ""),
            student_user_id=str(session.get("user_id") or ""),
            event="violation",
            detail={"ceiling": ceiling, "assessment": assessment},
        )
    else:
        assistance.record_event(
            school_id=str(session.get("school_id") or ""),
            lesson_id=str(policy_ctx.get("lesson_id") or ""),
            assignment_id=str(policy_ctx.get("assignment_id") or ""),
            student_user_id=str(session.get("user_id") or ""),
            event="turn",
            detail={"ceiling": ceiling},
        )
    tokens = quota.estimate_tokens(user_text=user_text, response_text=response_text)
    quota.settle(
        str(session.get("school_id") or ""), str(session.get("user_id") or ""), tokens
    )
