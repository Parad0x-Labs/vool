from __future__ import annotations

import re
from typing import Any

from .fast_live_info_news_topic import extract_news_topic


def render_news_response(*, query: str, notes: list[dict[str, Any]]) -> str:
    topic = extract_news_topic(query)
    lines = [f"Latest coverage on {topic}:" if topic else "Latest coverage:"]
    for note in list(notes or [])[:3]:
        summary = " ".join(str(note.get("summary") or "").split()).strip()
        fallback_title = str(note.get("result_title") or "").strip()
        url = str(note.get("result_url") or "").strip()
        domain = str(note.get("origin_domain") or "").strip()
        parts = [part.strip() for part in summary.split("|") if part.strip()]
        source = parts[0] if len(parts) >= 1 else domain
        published = parts[1] if len(parts) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]) else ""
        headline = parts[2] if len(parts) >= 3 else fallback_title or summary
        # Embed the link in the short source name ("[CaptainAltcoin](url)") so the UI shows a
        # clickable source instead of a raw redirect URL. Falls back to linking the headline when
        # no source name was parsed, and to plain text when there is no URL at all.
        source_label = f"[{source}]({url})" if (source and url) else source
        lead_parts = [item for item in (published, source_label) if item]
        lead = " | ".join(lead_parts)
        if lead:
            line = f"- {lead}: {headline}"
        elif url:
            line = f"- [{headline}]({url})"
        else:
            line = f"- {headline}"
        lines.append(line)
    return "\n".join(lines)
