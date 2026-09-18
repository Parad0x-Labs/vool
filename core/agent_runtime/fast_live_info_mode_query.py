from __future__ import annotations

from .fast_live_info_news_topic import extract_news_topic


def normalize_live_info_query(text: str, *, mode: str) -> str:
    clean = " ".join(str(text or "").split()).strip()
    lowered = clean.lower()
    if mode == "weather" and "forecast" not in lowered and "weather" in lowered:
        return f"{clean} forecast"
    if mode == "news":
        # Search (and title) the actual subject, not the raw message. "" signals no real topic so the
        # caller defers to the reasoning lane instead of searching filler. Append (not prepend) "latest
        # news": it still routes the downstream classifier to the RSS source, and the title extractor
        # reduces the query back to the subject -- a prepended "news" would be kept when the subject
        # itself is a real word (so "latest news Iran" would mis-title as "news Iran").
        topic = extract_news_topic(clean)
        return f"{topic} latest news" if topic else ""
    return clean
