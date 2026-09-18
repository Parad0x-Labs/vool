from __future__ import annotations

from typing import Any


def check_hive_duplicate(agent: Any, title: str, summary: str) -> dict[str, Any] | None:
    try:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        topics = agent.public_hive_bridge.list_public_topics(limit=50)
        title_tokens = set(title.lower().split())
        summary_tokens = set(summary.lower().split()[:30])
        all_tokens = title_tokens | summary_tokens
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "to",
            "for",
            "on",
            "in",
            "of",
            "and",
            "or",
            "how",
            "what",
            "why",
            "create",
            "task",
            "new",
            "hive",
        }
        meaningful = all_tokens - stop_words
        if not meaningful:
            return None
        for topic in topics:
            topic_date = str(topic.get("updated_at") or topic.get("created_at") or "")
            if topic_date and topic_date < cutoff:
                continue
            t_title = str(topic.get("title") or "").lower()
            t_summary = str(topic.get("summary") or "").lower()
            t_tokens = set(t_title.split()) | set(t_summary.split()[:30])
            overlap = meaningful & t_tokens
            if len(overlap) >= max(2, len(meaningful) * 0.5):
                return topic
    except Exception:
        pass
    return None
