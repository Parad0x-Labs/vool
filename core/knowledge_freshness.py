from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_LEASE_SECONDS = 180
DEFAULT_KNOWLEDGE_TTL_SECONDS = 900


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def expires_at(seconds: int = DEFAULT_KNOWLEDGE_TTL_SECONDS) -> str:
    return (utcnow() + timedelta(seconds=max(1, seconds))).isoformat()


def lease_expiry(seconds: int = DEFAULT_LEASE_SECONDS) -> str:
    return (utcnow() + timedelta(seconds=max(1, seconds))).isoformat()


def is_expired(ts: str | None) -> bool:
    if not ts:
        return False
    try:
        return datetime.fromisoformat(ts) < utcnow()
    except Exception:
        return False
