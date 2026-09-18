from __future__ import annotations

from datetime import datetime, timezone


def default_ttl_seconds(*, task_kind: str, output_mode: str) -> int:
    if output_mode in {"tool_intent", "json_object"}:
        return 1800
    if task_kind in {"classification", "normalization_assist"}:
        return 3600
    return 6 * 3600


def freshness_score(created_at: str | None, expires_at: str | None) -> float:
    if not created_at:
        return 0.35
    try:
        created = datetime.fromisoformat(created_at)
    except Exception:
        return 0.35
    now = datetime.now(timezone.utc)
    age_seconds = max(0.0, (now - created).total_seconds())
    score = max(0.2, 1.0 - min(age_seconds / (12 * 3600.0), 0.8))
    if expires_at:
        try:
            expires = datetime.fromisoformat(expires_at)
            if expires <= now:
                return 0.0
        except Exception:
            pass
    return score


def is_stale(candidate: dict[str, object]) -> bool:
    expires_at = candidate.get("expires_at")
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(str(expires_at)) <= datetime.now(timezone.utc)
    except Exception:
        return False


def should_revalidate(candidate: dict[str, object], *, min_freshness: float = 0.42) -> bool:
    if is_stale(candidate):
        return True
    return freshness_score(str(candidate.get("created_at") or ""), str(candidate.get("expires_at") or "")) < min_freshness
