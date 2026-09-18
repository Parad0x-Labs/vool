from __future__ import annotations

import re


def has_structured_hive_public_brief(text: str) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return False
    return bool(
        re.search(r"\b(?:task|goal|summary|title|name it|call it|called)\b\s*[:=-]", clean, re.IGNORECASE)
    )


def looks_like_raw_chat_transcript(text: str) -> bool:
    raw = str(text or "")
    if not raw.strip():
        return False
    hits = 0
    patterns = (
        r"(?m)^\s*(?:VOOL|You|User|Assistant|U)\s*$",
        r"\b\d{1,2}:\d{2}\b",
        r"(?m)^\s*/new\s*$",
        r"∅",
        r"(?m)^\s*(?:U|A)\s*$",
    )
    for pattern in patterns:
        if re.search(pattern, raw, re.IGNORECASE):
            hits += 1
    return hits >= 2


def strip_wrapping_quotes(text: str) -> str:
    clean = str(text or "").strip()
    if len(clean) >= 2 and clean[0] == clean[-1] and clean[0] in {'"', "'", "`"}:
        return clean[1:-1].strip()
    return clean
