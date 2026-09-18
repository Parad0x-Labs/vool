"""core/school/quota.py — student quotas with atomic reservation.

Two-step protocol (goal §14, §36):

    reserve()  — BEFORE generation. One ``BEGIN IMMEDIATE`` transaction reads
                 today's usage and bumps ``requests`` iff under the cap. The
                 request cap is therefore strictly atomic under concurrency:
                 N parallel requests against a 1-remaining cap yield exactly
                 one success. Token caps are checked pre-call against the
                 settled ledger (a request may overshoot by at most one
                 response's tokens; the settle records the truth).
    settle()   — AFTER the response, records actual tokens. Best-effort by
                 design: a crashed turn can over-count nothing (it never
                 settles) and under-count only its own tokens.

Quotas live in the SCHOOL policy layer (admin-set): ``student_daily_requests``,
``student_daily_tokens``. Keyed by opaque ``user_id`` — never a name.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from core.school import store
from core.school.store import school_db

DEFAULT_REQUESTS_PER_DAY = 200
DEFAULT_TOKENS_PER_DAY = 200_000


@dataclass(frozen=True)
class QuotaVerdict:
    allowed: bool
    reason: str = ""
    requests_used: int = 0
    requests_cap: int = 0
    tokens_used: int = 0
    tokens_cap: int = 0


def _caps(school_policy: dict[str, Any]) -> tuple[int, int]:
    try:
        req = int(school_policy.get("student_daily_requests") or DEFAULT_REQUESTS_PER_DAY)
    except (TypeError, ValueError):
        req = DEFAULT_REQUESTS_PER_DAY
    try:
        tok = int(school_policy.get("student_daily_tokens") or DEFAULT_TOKENS_PER_DAY)
    except (TypeError, ValueError):
        tok = DEFAULT_TOKENS_PER_DAY
    return max(0, req), max(0, tok)


def quota_verdict(school_id: str, user_id: str) -> QuotaVerdict:
    """Read-only verdict (UI display)."""
    school = store.get_school(school_id) or {}
    req_cap, tok_cap = _caps(school.get("policy") or {})
    day = date.today().isoformat()
    with school_db() as conn:
        row = conn.execute(
            "SELECT requests, tokens FROM school_quota_usage"
            " WHERE usage_day=? AND school_id=? AND user_id=?",
            (day, school_id, user_id),
        ).fetchone()
    used_req = int(row["requests"]) if row else 0
    used_tok = int(row["tokens"]) if row else 0
    return QuotaVerdict(
        allowed=(used_req < req_cap if req_cap else True) and (used_tok < tok_cap if tok_cap else True),
        reason="",
        requests_used=used_req,
        requests_cap=req_cap,
        tokens_used=used_tok,
        tokens_cap=tok_cap,
    )


def reserve(school_id: str, user_id: str) -> QuotaVerdict:
    """Atomically consume one request slot. The concurrency-safe pre-call gate."""
    school = store.get_school(school_id) or {}
    req_cap, tok_cap = _caps(school.get("policy") or {})
    day = date.today().isoformat()
    conn: sqlite3.Connection
    with school_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT requests, tokens FROM school_quota_usage"
            " WHERE usage_day=? AND school_id=? AND user_id=?",
            (day, school_id, user_id),
        ).fetchone()
        used_req = int(row["requests"]) if row else 0
        used_tok = int(row["tokens"]) if row else 0
        if req_cap and used_req >= req_cap:
            conn.execute("COMMIT")
            return QuotaVerdict(
                False,
                "Daily request quota exhausted for today.",
                used_req, req_cap, used_tok, tok_cap,
            )
        if tok_cap and used_tok >= tok_cap:
            conn.execute("COMMIT")
            return QuotaVerdict(
                False,
                "Daily token quota exhausted for today.",
                used_req, req_cap, used_tok, tok_cap,
            )
        conn.execute(
            "INSERT INTO school_quota_usage (usage_day, school_id, user_id, requests, tokens)"
            " VALUES (?,?,?,1,0)"
            " ON CONFLICT(usage_day, school_id, user_id)"
            " DO UPDATE SET requests = requests + 1",
            (day, school_id, user_id),
        )
        conn.execute("COMMIT")
    return QuotaVerdict(True, "", used_req + 1, req_cap, used_tok, tok_cap)


def settle(school_id: str, user_id: str, tokens: int) -> None:
    """Record the turn's actual tokens (post-call truth)."""
    if tokens <= 0:
        return
    day = date.today().isoformat()
    with school_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO school_quota_usage (usage_day, school_id, user_id, requests, tokens)"
            " VALUES (?,?,?,0,?)"
            " ON CONFLICT(usage_day, school_id, user_id)"
            " DO UPDATE SET tokens = tokens + ?",
            (day, school_id, user_id, int(tokens), int(tokens)),
        )
        conn.execute("COMMIT")


def estimate_tokens(*, user_text: str = "", response_text: str = "") -> int:
    """Offline estimate (the repo's char-class law of thumb): ~4 chars/token."""
    return (len(str(user_text or "")) + len(str(response_text or ""))) // 4
