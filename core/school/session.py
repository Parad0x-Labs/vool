"""core/school/session.py — signed school session tokens.

A school session is an Ed25519-signed bearer token minted by the server when a
user redeems a single-use access code (or joins a lesson with an expiring join
code). It binds the OPAQUE user id — never a name — plus role, school, and an
absolute expiry. Signature comes from the node signing key (network.signer),
the same key that signs honesty receipts, so a token cannot be forged outside
this process and verification is offline.

Server-side revocation is checked at every parse (user revoked flag), which
makes revocation instant: the signature proves authenticity, the store proves
standing. Expiry is absolute, so lesson authority dies with the lesson window
even if nobody calls end_lesson (goal §27: no stale permissions).
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from core.school import store

TOKEN_PREFIX = "vsl1."
DEFAULT_TTL_SECONDS = 8 * 3600  # a school day
STUDENT_LESSON_TTL_SECONDS = 4 * 3600


@dataclass(frozen=True)
class SchoolSession:
    school_id: str
    user_id: str
    role: str  # SCHOOL_ADMIN | TEACHER | STUDENT
    display_name: str
    lesson_id: str  # "" for non-lesson sessions (admin/teacher console use)
    exp: float  # absolute epoch seconds

    def expired(self, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= float(self.exp)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((str(data) + pad).encode("ascii"))


def _sign(payload_bytes: bytes) -> str:
    """Ed25519 signature via the node key (base64, as network.signer mints it)."""
    from network.signer import sign

    return sign(payload_bytes)


def _verify(payload_bytes: bytes, sig_b64: str, issuer_peer_id: str) -> bool:
    from network.signer import verify

    try:
        return bool(verify(payload_bytes, sig_b64, issuer_peer_id))
    except Exception:
        return False


def issue_session_token(
    *,
    school_id: str,
    user_id: str,
    role: str,
    display_name: str,
    lesson_id: str = "",
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    from network.signer import get_local_peer_id

    payload = {
        "v": 1,
        "school_id": school_id,
        "user_id": user_id,
        "role": role,
        "display_name": str(display_name or "")[:80],
        "lesson_id": str(lesson_id or ""),
        "iss": get_local_peer_id(),  # issuer node identity — verification anchor
        "exp": time.time() + int(ttl_seconds),
        "iat": time.time(),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return TOKEN_PREFIX + _b64(payload_bytes) + "." + _sign(payload_bytes)


def parse_session_token(token: str) -> SchoolSession | None:
    """Verify signature + expiry + standing. None on ANY failure (fail closed)."""
    try:
        raw = str(token or "").strip()
        if not raw.startswith(TOKEN_PREFIX):
            return None
        payload_b64, sig_b64 = raw[len(TOKEN_PREFIX) :].split(".", 1)
        payload_bytes = _unb64(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
        if not _verify(payload_bytes, sig_b64, str(payload.get("iss") or "")):
            return None
        session = SchoolSession(
            school_id=str(payload["school_id"]),
            user_id=str(payload["user_id"]),
            role=str(payload["role"]),
            display_name=str(payload.get("display_name") or ""),
            lesson_id=str(payload.get("lesson_id") or ""),
            exp=float(payload["exp"]),
        )
        if session.expired():
            return None
        user = store.get_user(session.user_id)
        if user is None or user.get("school_id") != session.school_id:
            return None
        if bool(user.get("revoked")):
            return None
        if user.get("role") != session.role:
            return None
        return session
    except Exception:
        return None


def session_from_headers(headers: dict | None) -> SchoolSession | None:
    """Read the school session from X-School-Session header or Cookie."""
    if not headers:
        return None
    headers = {str(k).lower(): v for k, v in headers.items()}
    token = str(headers.get("x-school-session") or "").strip()
    if not token:
        cookie = str(headers.get("cookie") or "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "vool_school_session":
                token = value.strip()
                break
    if not token:
        return None
    return parse_session_token(token)
