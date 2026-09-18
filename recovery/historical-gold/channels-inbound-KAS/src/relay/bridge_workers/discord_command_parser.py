from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def extract_discord_command_text(
    content: Any,
    *,
    mention_matches: Callable[[str], bool] | None = None,
) -> str | None:
    """Extract the accepted Discord command grammar without granting authority."""

    text = str(content or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("!vool "):
        return text[7:].strip()
    if lowered.startswith("vool:"):
        return text[6:].strip()
    if lowered.startswith("vool,"):
        return text[6:].strip()
    match = re.match(r"^<@!?([0-9]{1,20})>(.*)$", text, re.DOTALL)
    if match is None or mention_matches is None:
        return None
    try:
        authorized = mention_matches(match.group(1))
    except (TypeError, ValueError):
        return None
    if authorized is not True:
        return None
    return match.group(2).strip()


__all__ = ["extract_discord_command_text"]
