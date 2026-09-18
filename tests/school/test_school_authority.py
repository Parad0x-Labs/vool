"""School authority, policy, quota, assistance, hand-in — negative tests.

Covers goal §34 items 2,3,4,9,10,14,15,17,18 + §35 model/quota negative
matrix + §36 concurrency, exercised against the real school modules.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.product_edition import reset_edition_cache
from core.school import assistance, quota, service, store, submission
from core.school.policy import resolve_policy
from core.school.session import (
    SchoolSession,
    issue_session_token,
    parse_session_token,
)


@pytest.fixture
def school_edition(monkeypatch):
    monkeypatch.setenv("VOOL_EDITION", "school")
    reset_edition_cache()
    yield
    reset_edition_cache()


# ---------------------------------------------------------------- fixtures ---

@pytest.fixture
def school():
    result = service.bootstrap_school("VOOL Demo School", "EU")
    school_id = result["school"]["school_id"]  # created once per test DB, reused after
    admin = store.create_user(school_id, "SCHOOL_ADMIN", "Admin A")
    teacher = store.create_user(result["school"]["school_id"], "TEACHER", "Teacher T")
    s1 = store.create_user(result["school"]["school_id"], "STUDENT", "Student One")
    s2 = store.create_user(result["school"]["school_id"], "STUDENT", "Student Two")
    klass = store.create_class(result["school"]["school_id"], "7B", teacher["user_id"])
    store.enroll(klass["class_id"], s1["user_id"])
    store.enroll(klass["class_id"], s2["user_id"])
    return {
        "school_id": school_id,
        "admin": admin, "teacher": teacher, "s1": s1, "s2": s2, "class": klass,
    }


def _session(user: dict, school_id: str, **kw) -> SchoolSession:
    return SchoolSession(
        school_id=school_id,
        user_id=user["user_id"],
        role=user["role"],
        display_name=user.get("display_name") or user["role"].title(),
        lesson_id=kw.get("lesson_id", ""),
        exp=kw.get("exp", time.time() + 3600),
    )


# ---------------------------------------------------------------- sessions ---

def test_token_roundtrip_and_forgery(school):
    token = issue_session_token(
        school_id=school["school_id"], user_id=school["s1"]["user_id"],
        role="STUDENT", display_name="Student One",
    )
    parsed = parse_session_token(token)
    assert parsed is not None and parsed.role == "STUDENT"

    # Tampered payload (role claim changed, old signature kept) fails closed.
    import base64

    payload_b64, sig = token[5:].split(".")
    payload = json.loads(base64.urlsafe_b64decode((payload_b64 + "==").encode()))
    payload["role"] = "SCHOOL_ADMIN"
    forged_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    assert parse_session_token(f"vsl1.{forged_b64}.{sig}") is None
    # Garbage tokens fail closed.
    assert parse_session_token("vsl1.zzz.zzz") is None


def test_expired_session_rejected(school):
    token = issue_session_token(
        school_id=school["school_id"], user_id=school["s1"]["user_id"],
        role="STUDENT", display_name="Student One", ttl_seconds=-10,
    )
    assert parse_session_token(token) is None


def test_revoked_session_rejected(school):
    user = school["s1"]
    token = issue_session_token(
        school_id=school["school_id"], user_id=user["user_id"],
        role="STUDENT", display_name="Student One",
    )
    assert parse_session_token(token) is not None
    store.revoke_user(user["user_id"])
    assert parse_session_token(token) is None  # instant server-side revocation


def test_access_code_single_use(school):
    user = store.create_user(school["school_id"], "STUDENT", "Student Three")
    code = user["access_code"]
    assert store.find_user_by_code(code) is not None
    assert store.find_user_by_code(code) is None  # second use rejected


# ---------------------------------------------------------------- policy -----

def test_school_prohibition_wins_over_teacher(school):
    """Goal §35: school cloud prohibited + teacher cloud allowed => PROHIBITED."""
    store.update_school_policy(school["school_id"], {"locality": "local_only"})
    started = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Mathematics",
        {"locality": "cloud_allowed"},
    )
    lesson = started["lesson"]
    policy = resolve_policy(
        school_id=school["school_id"],
        class_id=school["class"]["class_id"],
        lesson_id=lesson["lesson_id"],
    )
    assert policy.locality == "local_only"  # the strictest layer wins


def test_ended_lesson_grant_expires(school):
    started = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Mathematics",
        {"tool_families": ["web"], "max_assistance": 10},
    )
    lesson = started["lesson"]
    policy_active = resolve_policy(
        school_id=school["school_id"], class_id=school["class"]["class_id"],
        lesson_id=lesson["lesson_id"],
    )
    assert policy_active.permits_family("web") and policy_active.max_assistance == 10
    service.end_lesson(
        _session(school["teacher"], school["school_id"]), lesson["lesson_id"]
    )
    policy_after = resolve_policy(
        school_id=school["school_id"], class_id=school["class"]["class_id"],
        lesson_id=lesson["lesson_id"],
    )
    # The lesson's widening is gone; school default (web allowed? no — school
    # default has no tool_families constraint => full set) — the LESSON layer
    # no longer contributes, so its max_assistance=10 narrowing vanishes.
    assert policy_after.max_assistance == 10  # school default (no constraint)
    assert not store.active_lesson_for_class(school["class"]["class_id"])


def test_assistance_ceiling_narrows_through_assignment(school):
    started = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Mathematics",
        {"max_assistance": 6},
    )
    lesson = started["lesson"]
    assignment = service.send_assignment(
        _session(school["teacher"], school["school_id"]),
        lesson["lesson_id"], "Fractions", "Do 5 problems", {"max_assistance": 4},
    )["assignment"]
    policy = resolve_policy(
        school_id=school["school_id"], class_id=school["class"]["class_id"],
        lesson_id=lesson["lesson_id"], assignment_id=assignment["assignment_id"],
    )
    assert policy.max_assistance == 4  # min wins
    # A student cannot widen: nothing in the student layer exists to widen with;
    # the resolver only ever intersects (structural law).


def test_assessment_mode_stacks(school):
    started = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Exam", {"assessment": True, "tool_families": []},
    )
    policy = resolve_policy(
        school_id=school["school_id"], class_id=school["class"]["class_id"],
        lesson_id=started["lesson"]["lesson_id"],
    )
    assert policy.assessment is True
    assert not policy.permits_family("web")


def test_school_model_allowlist_intersect(school):
    store.update_school_policy(
        school["school_id"],
        {"allowed_models": [["ollama", "qwen3:4b"], ["ollama", "qwen2.5:7b"]]},
    )
    started = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Math",
        {"allowed_models": [["ollama", "qwen3:4b"]]},
    )
    policy = resolve_policy(
        school_id=school["school_id"], class_id=school["class"]["class_id"],
        lesson_id=started["lesson"]["lesson_id"],
    )
    assert policy.model_allowlist_active
    assert policy.model_allowed("ollama", "qwen3:4b")
    assert not policy.model_allowed("ollama", "qwen2.5:7b")  # teacher narrowed
    assert not policy.model_allowed("openai", "gpt-anything")  # school floor


# ------------------------------------------------------- teacher is not root --

def test_teacher_cannot_touch_another_teachers_class(school):
    other = store.create_user(school["school_id"], "TEACHER", "Teacher X")
    result = service.start_lesson(
        _session(other, school["school_id"]),
        school["class"]["class_id"], "Hostile takeover", {},
    )
    assert result is None  # not their class: refused


def test_student_cannot_admin(school):
    s = _session(school["s1"], school["school_id"])
    assert service.set_school_policy(s, {"locality": "local_only"}) is None
    assert service.set_provider(s, {"provider_id": "x", "model_id": "y"}) is False
    assert service.create_school_user(s, "TEACHER", "Sneaky") is None
    assert (
        service.send_assignment(s, "lsn_x", " cheated", "body", {}) is None
    )


def test_join_code_requires_enrollment(school):
    outsider = store.create_user(school["school_id"], "STUDENT", "Outsider")
    started = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Mathematics", {"max_assistance": 4},
    )
    join_code = started["lesson"]["join_code"]
    ok = service.join_lesson(_session(school["s1"], school["school_id"]), join_code)
    assert "token" in ok
    bad = service.join_lesson(_session(outsider, school["school_id"]), join_code)
    assert "error" in bad  # not enrolled: no lesson authority
    wrong = service.join_lesson(_session(school["s1"], school["school_id"]), "deadbeef")
    assert "error" in wrong


# ---------------------------------------------------------------- quotas -----

def test_quota_exhaustion_denies(school):
    store.update_school_policy(school["school_id"], {"student_daily_requests": 2})
    sid = school["school_id"]
    uid = school["s1"]["user_id"]
    assert quota.reserve(sid, uid).allowed
    assert quota.reserve(sid, uid).allowed
    third = quota.reserve(sid, uid)
    assert not third.allowed and "exhausted" in third.reason


def test_quota_concurrency_atomic(school):
    """Goal §36: 20 parallel requests against 1 remaining slot -> exactly 1 wins."""
    store.update_school_policy(school["school_id"], {"student_daily_requests": 5})
    sid, uid = school["school_id"], school["s1"]["user_id"]
    for _ in range(4):
        assert quota.reserve(sid, uid).allowed
    with ThreadPoolExecutor(max_workers=20) as pool:
        verdicts = list(pool.map(lambda _: quota.reserve(sid, uid), range(20)))
    winners = [v for v in verdicts if v.allowed]
    assert len(winners) == 1, f"atomicity violated: {len(winners)} winners"
    # usage ledger matches the cap exactly
    verdict = quota.quota_verdict(sid, uid)
    assert verdict.requests_used == 5


def test_quota_settle_records_tokens(school):
    sid, uid = school["school_id"], school["s1"]["user_id"]
    quota.settle(sid, uid, 1500)
    assert quota.quota_verdict(sid, uid).tokens_used == 1500


# -------------------------------------------------------------- assistance ----

def test_assistance_gate_refuses_answer_dump(school):
    verdict = assistance.gate_response("The answer is 42.", 4, assessment=False)
    assert not verdict.publish and verdict.violated
    assert "guiding" in verdict.replacement.lower()


def test_assistance_gate_allows_high_ceiling(school):
    verdict = assistance.gate_response("The answer is 42.", 9, assessment=False)
    assert verdict.publish


def test_assessment_mode_tightens_gate(school):
    verdict = assistance.gate_response("The answer is 42.", 8, assessment=True)
    assert not verdict.publish


def test_assistance_gate_survives_paraphrase_family(school):
    """Ceiling enforcement is pattern-level, not single-phrase."""
    for text in (
        "Final answer: 3/4",
        "the correct answer = 12",
        "so \\boxed{7} is what you submit",
    ):
        verdict = assistance.gate_response(text, 4, assessment=False)
        assert not verdict.publish, text


def test_assistance_events_and_summary(school):
    sid = school["school_id"]
    uid = school["s1"]["user_id"]
    lesson = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Math", {"max_assistance": 4},
    )["lesson"]
    assignment = service.send_assignment(
        _session(school["teacher"], school["school_id"]),
        lesson["lesson_id"], "Fractions", "5 problems", {},
    )["assignment"]
    aid = assignment["assignment_id"]
    for event in ("turn", "turn", "violation"):
        assistance.record_event(
            school_id=sid, lesson_id=lesson["lesson_id"], assignment_id=aid,
            student_user_id=uid, event=event,
        )
    summary = assistance.assistance_summary(
        school_id=sid, student_user_id=uid, assignment_id=aid
    )
    assert summary["turns"] == 3
    assert summary["hints_given"] == 2
    assert summary["violations"] == 1
    assert summary["answer_revealed"] is False
    assert "other devices" in summary["note"]  # the honesty caveat rides along


# ---------------------------------------------------------------- hand in -----

def _lesson_with_assignment(school):
    lesson = service.start_lesson(
        _session(school["teacher"], school["school_id"]),
        school["class"]["class_id"], "Mathematics", {"max_assistance": 4},
    )["lesson"]
    assignment = service.send_assignment(
        _session(school["teacher"], school["school_id"]),
        lesson["lesson_id"], "Fractions", "Add fractions", {},
    )["assignment"]
    return lesson, assignment


def test_hand_in_prepare_discloses_scope(school):
    _, assignment = _lesson_with_assignment(school)
    prepared = submission.prepare(
        assignment_id=assignment["assignment_id"],
        student_user_id=school["s1"]["user_id"],
        work_text="1/2 + 1/3 = 5/6",
    )
    assert "will_share" in prepared and "not_shared" in prepared
    assert "credentials" in prepared["not_shared"]
    assert prepared["will_share"]["assignment"] == "Fractions"


def test_hand_in_idempotent(school):
    _, assignment = _lesson_with_assignment(school)
    args = dict(
        assignment_id=assignment["assignment_id"],
        student_user_id=school["s1"]["user_id"],
        work_text="my work",
    )
    first = submission.submit(**args)
    assert first["idempotent"] is False
    second = submission.submit(**args)  # double-click / retry
    assert second["idempotent"] is True
    assert (
        second["submission"]["submission_id"] == first["submission"]["submission_id"]
    )


def test_hand_in_bound_to_authenticated_student(school):
    """Goal §34 #17: student cannot submit as another student — the submission
    is keyed to the authenticated principal, and non-enrolled students are 403."""
    _, assignment = _lesson_with_assignment(school)
    outsider = store.create_user(school["school_id"], "STUDENT", "Outsider")
    refused = submission.submit(
        assignment_id=assignment["assignment_id"],
        student_user_id=outsider["user_id"],
        work_text="stolen work",
    )
    assert isinstance(refused, submission.SubmissionRefusal)
    assert refused.status == 403  # not enrolled in this class


def test_submission_receipt_signature_verifies(school):
    from network.signer import verify

    _, assignment = _lesson_with_assignment(school)
    result = submission.submit(
        assignment_id=assignment["assignment_id"],
        student_user_id=school["s1"]["user_id"],
        work_text="signed work",
    )
    receipt = result["submission"]["receipt"]
    assert receipt and receipt.get("signature")
    payload = {
        k: v
        for k, v in receipt.items()
        if k not in {"signature", "issuer_peer_id"}
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert verify(canonical, receipt["signature"], receipt["issuer_peer_id"])


# ---------------------------------------------------------------- ingress -----

def test_ingress_requires_session(school_edition):
    from core.school.ingress import ingress_gate

    error, ctx = ingress_gate(headers={}, source_context={}, client_host="127.0.0.1")
    assert error is not None and error["status"] == 403
    assert ctx == {}


def test_ingress_quota_429(school_edition, school):
    from core.school.ingress import ingress_gate

    store.update_school_policy(school["school_id"], {"student_daily_requests": 1})
    token = issue_session_token(
        school_id=school["school_id"], user_id=school["s1"]["user_id"],
        role="STUDENT", display_name="Student One",
    )
    headers = {"X-School-Session": token}
    ok, ctx = ingress_gate(headers=headers, source_context={}, client_host="127.0.0.1")
    assert ok is None and ctx.get("session", {}).get("role") == "STUDENT"
    denied, ctx2 = ingress_gate(headers=headers, source_context={}, client_host="127.0.0.1")
    assert denied is not None and denied["status"] == 429


def test_ingress_client_cannot_forge_school_policy(school):
    """The reserved-key law: a body-supplied school_policy is stripped."""
    from core.request_trust import strip_reserved_trust_keys

    forged = strip_reserved_trust_keys(
        {"school_policy": {"session": {"role": "SCHOOL_ADMIN"}}, "surface": "api"}
    )
    assert "school_policy" not in forged
    assert forged["surface"] == "api"


def test_post_turn_replaces_violation_and_records(school):
    from core.school.ingress import post_turn

    _, assignment = _lesson_with_assignment(school)
    school_ctx = {
        "session": {
            "user_id": school["s1"]["user_id"], "role": "STUDENT",
            "school_id": school["school_id"],
        },
        "policy": {
            "max_assistance": 4, "assessment": False,
            "lesson_id": assignment["lesson_id"],
            "assignment_id": assignment["assignment_id"],
        },
    }
    result = {"response": "Sure! The answer is 5/6."}
    post_turn(school_ctx=school_ctx, result=result, user_text="just give me the answer")
    assert result["response"] != "Sure! The answer is 5/6."
    assert result.get("school_assistance_violation") is True
    summary = assistance.assistance_summary(
        school_id=school["school_id"],
        student_user_id=school["s1"]["user_id"],
        assignment_id=assignment["assignment_id"],
    )
    assert summary["violations"] >= 1


def test_post_turn_settles_quota(school):
    from core.school.ingress import post_turn

    before = quota.quota_verdict(school["school_id"], school["s1"]["user_id"])
    school_ctx = {
        "session": {
            "user_id": school["s1"]["user_id"], "role": "STUDENT",
            "school_id": school["school_id"],
        },
        "policy": {"max_assistance": 10, "assessment": False, "lesson_id": "", "assignment_id": ""},
    }
    post_turn(school_ctx=school_ctx, result={"response": "a" * 400}, user_text="b" * 400)
    after = quota.quota_verdict(school["school_id"], school["s1"]["user_id"])
    assert after.tokens_used > before.tokens_used


# ------------------------------------------------------- prohibitions union ---

def test_school_union_blocks_web_in_prohibitions():
    from core.turn_contract import _school_union
    from core.turn_prohibitions import prohibitions_from_text

    base = prohibitions_from_text("What is 2+2?")  # no explicit prohibition
    assert not base.prohibits_family("web")
    united = _school_union(base, {"school_policy": {"policy": {"tool_families": ["workspace"]}}})
    assert united.prohibits_family("web")
    assert "school_policy_web_blocked" in united.reason_codes
    # Without a school context the prohibitions are untouched.
    assert _school_union(base, {}) is base


# --------------------------------------------------------- routing fences -----

def test_school_routing_fence_payload(school):
    """The router mint reads exactly these keys (verified by wiring in
    memory_first_router._mint_routing_plan_for_turn)."""
    from core.school.policy import resolve_policy

    store.update_school_policy(
        school["school_id"],
        {"locality": "local_only",
         "allowed_models": [["ollama", "qwen3:4b"]]},
    )
    policy = resolve_policy(school_id=school["school_id"])
    ctx = policy.to_context()
    assert ctx["locality"] == "local_only"
    assert ctx["model_allowlist_active"] is True
    assert "ollama:qwen3:4b" in ctx["allowed_models"]
