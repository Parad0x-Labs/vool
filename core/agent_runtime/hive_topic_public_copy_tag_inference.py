from __future__ import annotations

import re
from typing import Any

from core.agent_runtime.hive_topic_public_copy_tag_stopwords import HIVE_TOPIC_TAG_STOPWORDS


def infer_hive_topic_tags(agent: Any, title: str) -> list[str]:
    raw_tokens = re.findall(r"[a-z0-9]+", str(title or "").lower())
    tags: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        if len(token) < 3 and token not in {"ai", "ux", "ui", "vm", "os"}:
            continue
        if token in HIVE_TOPIC_TAG_STOPWORDS:
            continue
        normalized = agent._normalize_hive_topic_tag(token)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tags.append(normalized)
        if len(tags) >= 6:
            break
    return tags
