from __future__ import annotations

import re


def normalize_hive_topic_tag(raw: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", str(raw or "").strip().lower()).strip("-")
    if len(clean) < 2 or len(clean) > 32:
        return ""
    return clean
