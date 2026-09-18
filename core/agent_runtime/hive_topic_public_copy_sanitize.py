from __future__ import annotations

import re

from core.task_router import redact_text


def sanitize_public_hive_text(text: str) -> str:
    sanitized = redact_text(str(text or ""))
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized
