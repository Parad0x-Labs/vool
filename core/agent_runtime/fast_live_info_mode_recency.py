from __future__ import annotations

from core.task_router import looks_like_live_recency_lookup


def requires_ultra_fresh_insufficient_evidence(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or not looks_like_live_recency_lookup(lowered):
        return False
    if not any(
        marker in lowered
        for marker in (
            " minute ago",
            " minutes ago",
            " just now",
            " just happened",
        )
    ):
        return False
    return any(
        marker in lowered
        for marker in (
            "what happened",
            "markets",
            "market",
            "headline",
            "headlines",
            "news",
            "global",
        )
    )


def ultra_fresh_insufficient_evidence_response(*, query: str) -> str:
    clean_query = " ".join(str(query or "").split()).strip() or "that"
    return (
        f"I can't verify `{clean_query}` with enough confidence at minute-by-minute resolution from this runtime. "
        "For claims about what happened just now or five minutes ago, I should treat the result as insufficient evidence unless a timestamped live source confirms it directly."
    )
