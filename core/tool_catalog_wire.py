"""The prose tool catalog, as it leaves for a model that is also handed the tools as native functions.

The catalog a tool step's prompt carries (core.prompt_normalizer._tool_intent_catalog_text) lists every
offered tool with its arguments and description, for a lane that can only read about its tools. A lane
that is handed native functions gets the very same names, arguments and descriptions in the payload's
``tools``; there the prose list is the same text a second time. Intel smoke on 94b9dad5d (2026-11-24,
qwen3:8b on a CPU that reads 7 to 10 prompt tokens a second): the list was about 1,100 of a contacts
step's 3,972 tokens, and each cold tool step took 7 to 9 minutes.

So the adapter, once its payload carries native tools, sends the catalog without the per-tool list:
the opener that maps each canonical operation name to its function name, the closing rules and any
skill guidance stay. A lane without native tools keeps the whole catalog, which is its only list.
"""
from __future__ import annotations

from typing import Any

#: The catalog's first words when it lists tools (the renderer's own opener).
CATALOG_OPENER_WITH_SPECS = "When native functions are supplied, use these exact function names and declared parameters."
#: Where the part only a lane without native functions needs begins.
PROSE_ONLY_START = "Without native functions, return JSON with the canonical intent and arguments."
#: Where the catalog's closing rules begin, after the per-tool list.
CLOSING_RULES_START = "If no tool is needed or you are done after real tool work, use respond.direct"


def catalog_for_native_wire(text: str) -> str:
    """``text`` without its per-tool list when it holds a tool catalog that lists tools; otherwise unchanged."""
    opener = text.find(CATALOG_OPENER_WITH_SPECS) if isinstance(text, str) else -1
    if opener < 0:
        return text
    start = text.find(" " + PROSE_ONLY_START, opener)
    end = text.find(CLOSING_RULES_START, start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        return text
    return text[:start] + " " + text[end:]


def messages_for_native_wire(messages: list[Any]) -> list[Any]:
    """The payload's messages with each system tool catalog sent without its per-tool list.

    Called only for a payload that carries native ``tools``. A message whose content is not plain text
    is left as it is."""
    out: list[Any] = []
    for message in messages or []:
        if (isinstance(message, dict) and message.get("role") == "system"
                and isinstance(message.get("content"), str)):
            slim = catalog_for_native_wire(message["content"])
            if slim != message["content"]:
                message = {**message, "content": slim}
        out.append(message)
    return out


__all__ = [
    "CATALOG_OPENER_WITH_SPECS",
    "CLOSING_RULES_START",
    "PROSE_ONLY_START",
    "catalog_for_native_wire",
    "messages_for_native_wire",
]
